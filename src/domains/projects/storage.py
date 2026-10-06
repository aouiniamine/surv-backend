import asyncio
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from starlette.requests import Request

PUBLIC_ID_PATTERN = re.compile(r"^[a-z0-9]{7}$")
BACKUP_KEY_PATTERN = re.compile(r"^[0-9a-f]{32}$")
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024
MAX_ENTRIES = 10_000
CHUNK_BYTES = 1024 * 1024
MAX_REWRITE_BYTES = 16 * 1024 * 1024
ROOT_FILE_URL = re.compile(rb"(?P<lead>[\"'`=(])/(?P<path>[a-zA-Z0-9._~%/-]+)")


class InvalidAppArchive(ValueError):
    """The uploaded application archive cannot be deployed."""


class AppArchiveTooLarge(ValueError):
    """The uploaded application exceeds the deployment limits."""


class BackupArchiveMissing(FileNotFoundError):
    """A recorded backup no longer has an archive on disk."""


class ProjectStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def project_dir(self, public_id: str) -> Path:
        if not PUBLIC_ID_PATTERN.fullmatch(public_id):
            raise ValueError("Invalid project public ID")
        return self.root / public_id

    def ensure_public(self, public_id: str) -> None:
        (self.project_dir(public_id) / "public").mkdir(parents=True, exist_ok=True)

    async def deploy(
        self,
        public_id: str,
        request: Request,
        record_deployment: Callable[[str | None], Awaitable[list[str]]],
        *,
        backup_previous: bool,
    ) -> None:
        project_dir = self.project_dir(public_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        archive_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=project_dir, suffix=".zip", delete=False
            ) as archive:
                archive_path = Path(archive.name)
                received = 0
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > MAX_ARCHIVE_BYTES:
                        raise AppArchiveTooLarge("ZIP archive exceeds 20 MiB")
                    await asyncio.to_thread(archive.write, chunk)
            if received == 0:
                raise InvalidAppArchive("ZIP archive is empty")
            staging = await asyncio.to_thread(self._prepare, public_id, archive_path)
            await self._publish_and_record(
                public_id, archive_path, staging, record_deployment, backup_previous
            )
        finally:
            if archive_path is not None:
                archive_path.unlink(missing_ok=True)

    async def restore(
        self,
        public_id: str,
        backup_path: str,
        record_deployment: Callable[[str | None], Awaitable[list[str]]],
    ) -> None:
        relative = Path(backup_path)
        if (
            relative.parent != Path("backups")
            or relative.suffix != ".zip"
            or not BACKUP_KEY_PATTERN.fullmatch(relative.stem)
        ):
            raise InvalidAppArchive("Backup archive path is invalid")
        archive = self.project_dir(public_id) / relative
        if not archive.is_file():
            raise BackupArchiveMissing("Backup archive is missing")
        staging = await asyncio.to_thread(self._prepare, public_id, archive)
        await self._publish_and_record(
            public_id, archive, staging, record_deployment, backup_previous=True
        )

    def _prepare(self, public_id: str, archive_path: Path) -> Path:
        project_dir = self.project_dir(public_id)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=project_dir))
        try:
            self._extract(archive_path, staging)
            if not (staging / "index.html").is_file():
                raise InvalidAppArchive("ZIP archive must contain index.html at its root")
            return staging
        except BaseException:
            shutil.rmtree(staging)
            raise

    async def _publish_and_record(
        self,
        public_id: str,
        archive_path: Path,
        staging: Path,
        record_deployment: Callable[[str | None], Awaitable[list[str]]],
        backup_previous: bool,
    ) -> None:
        project_dir = self.project_dir(public_id)
        public = project_dir / "public"
        current_archive = project_dir / "current.zip"
        previous_public = project_dir / f".previous-{uuid.uuid4().hex}"
        backup_relative = Path("backups") / f"{uuid.uuid4().hex}.zip"
        backup_public = project_dir / "backups" / backup_relative.stem / "public"
        previous_archive = (
            project_dir / backup_relative
            if backup_previous
            else project_dir / f".previous-{uuid.uuid4().hex}.zip"
        )
        expired_paths: list[str] = []
        published = False
        try:
            if backup_previous:
                if not current_archive.is_file():
                    raise RuntimeError("Deployed project archive is missing")
                if not public.is_dir():
                    raise RuntimeError("Deployed project files are missing")
                previous_archive.parent.mkdir(parents=True, exist_ok=True)
            if public.exists():
                public.rename(previous_public)
            try:
                if current_archive.exists():
                    current_archive.rename(previous_archive)
                staging.rename(public)
                await asyncio.to_thread(shutil.copyfile, archive_path, current_archive)
                if backup_previous:
                    backup_public.parent.mkdir(parents=True, exist_ok=True)
                    previous_public.rename(backup_public)
                expired_paths = await record_deployment(
                    backup_relative.as_posix() if backup_previous else None
                )
                published = True
            finally:
                if not published:
                    if public.exists():
                        shutil.rmtree(public)
                    current_archive.unlink(missing_ok=True)
                    if backup_public.exists():
                        backup_public.rename(previous_public)
                    if previous_public.exists():
                        previous_public.rename(public)
                    if previous_archive.exists():
                        previous_archive.rename(current_archive)
                    if backup_public.parent.exists():
                        backup_public.parent.rmdir()
        finally:
            if staging.exists():
                shutil.rmtree(staging)
            if published:
                if previous_public.exists():
                    shutil.rmtree(previous_public)
                if not backup_previous:
                    previous_archive.unlink(missing_ok=True)
                for relative in expired_paths:
                    path = Path(relative)
                    if path.parent == Path("backups") and path.suffix == ".zip":
                        (project_dir / path).unlink(missing_ok=True)
                        shutil.rmtree(project_dir / "backups" / path.stem, ignore_errors=True)

    @staticmethod
    def _extract(archive_path: Path, staging: Path) -> None:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                entries = archive.infolist()
                if len(entries) > MAX_ENTRIES:
                    raise AppArchiveTooLarge("ZIP archive has too many files")
                total = 0
                for entry in entries:
                    name = entry.filename
                    path = PurePosixPath(name)
                    mode = (entry.external_attr >> 16) & 0o170000
                    if (
                        not name
                        or name.startswith("/")
                        or "\\" in name
                        or ":" in path.parts[0]
                        or any(part in ("", ".", "..") for part in name.rstrip("/").split("/"))
                        or mode not in (0, stat.S_IFREG, stat.S_IFDIR)
                    ):
                        raise InvalidAppArchive("ZIP archive contains an unsafe path or file type")
                    target = staging.joinpath(*path.parts)
                    if entry.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    total += entry.file_size
                    if total > MAX_EXTRACTED_BYTES:
                        raise AppArchiveTooLarge("Extracted application exceeds 1 GiB")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(entry) as source, target.open("wb") as destination:
                        ProjectStorage._copy_limited(source, destination, entry.file_size)
        except (zipfile.BadZipFile, RuntimeError, EOFError) as exc:
            raise InvalidAppArchive("Invalid ZIP archive") from exc

    @staticmethod
    def _copy_limited(source: BinaryIO, destination: BinaryIO, expected_size: int) -> None:
        copied = 0
        while chunk := source.read(CHUNK_BYTES):
            copied += len(chunk)
            if copied > expected_size or copied > MAX_EXTRACTED_BYTES:
                raise AppArchiveTooLarge("ZIP entry exceeds its declared size")
            destination.write(chunk)

    def asset(self, public_id: str, request_path: str) -> Path | None:
        public = self.project_dir(public_id) / "public"
        return self._asset_from_directory(public, request_path)

    def backup_asset(self, public_id: str, backup_key: str, request_path: str) -> Path | None:
        if not BACKUP_KEY_PATTERN.fullmatch(backup_key):
            return None
        project_dir = self.project_dir(public_id)
        public = project_dir / "backups" / backup_key / "public"
        archive = project_dir / "backups" / f"{backup_key}.zip"
        if not public.is_dir() and archive.is_file():
            public.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=public.parent))
            try:
                self._extract(archive, staging)
                if not (staging / "index.html").is_file():
                    return None
                try:
                    staging.rename(public)
                except FileExistsError:
                    pass
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        return self._asset_from_directory(public, request_path)

    @staticmethod
    def _asset_from_directory(public: Path, request_path: str) -> Path | None:
        if not public.is_dir():
            return None
        requested = PurePosixPath(request_path)
        if (
            request_path.startswith("/")
            or "\\" in request_path
            or any(part == ".." for part in requested.parts)
        ):
            return None
        target = (public / requested).resolve()
        if target.is_relative_to(public.resolve()):
            if target.is_file():
                return target
            if target.is_dir() and (target / "index.html").is_file():
                return target / "index.html"
        fallback = public / "index.html"
        return fallback if not requested.suffix and fallback.is_file() else None

    def rewritten_asset(self, public_id: str, asset: Path) -> bytes | None:
        return self._rewritten_asset(
            asset,
            self.project_dir(public_id) / "public",
            f"/app/{public_id}/".encode(),
        )

    def rewritten_backup_asset(self, public_id: str, backup_key: str, asset: Path) -> bytes | None:
        if not BACKUP_KEY_PATTERN.fullmatch(backup_key):
            return None
        return self._rewritten_asset(
            asset,
            self.project_dir(public_id) / "backups" / backup_key / "public",
            f"/app-backups/{public_id}/{backup_key}/".encode(),
        )

    @staticmethod
    def _rewritten_asset(asset: Path, public: Path, prefix: bytes) -> bytes | None:
        if asset.suffix.lower() not in {".html", ".js", ".css"}:
            return None
        if asset.stat().st_size > MAX_REWRITE_BYTES:
            return None
        content = asset.read_bytes()

        def replace(match: re.Match[bytes]) -> bytes:
            relative_path = match.group("path").decode("ascii")
            if any(part in ("", ".", "..") for part in relative_path.split("/")):
                return match.group()
            if (public / relative_path).is_file():
                return match.group("lead") + prefix + match.group("path")
            return match.group()

        rewritten = ROOT_FILE_URL.sub(replace, content)
        if asset.suffix.lower() == ".html":
            rewritten = rewritten.replace(b'"basename":"/"', b'"basename":"' + prefix + b'"')
        return rewritten if rewritten != content else None
