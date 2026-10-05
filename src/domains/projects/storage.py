import asyncio
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from starlette.requests import Request

PUBLIC_ID_PATTERN = re.compile(r"^[a-z0-9]{7}$")
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024
MAX_ENTRIES = 10_000
CHUNK_BYTES = 1024 * 1024
MAX_REWRITE_BYTES = 16 * 1024 * 1024
ROOT_FILE_URL = re.compile(rb"(?P<lead>[\"'`=(])/(?P<path>[a-zA-Z0-9._~%/-]+)")


class InvalidAppArchive(ValueError):
    """The uploaded application archive cannot be deployed."""


class AppArchiveTooLarge(ValueError):
    """The uploaded application exceeds the deployment limits."""


class ProjectStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def project_dir(self, public_id: str) -> Path:
        if not PUBLIC_ID_PATTERN.fullmatch(public_id):
            raise ValueError("Invalid project public ID")
        return self.root / public_id

    def ensure_public(self, public_id: str) -> None:
        (self.project_dir(public_id) / "public").mkdir(parents=True, exist_ok=True)

    async def deploy(self, public_id: str, request: Request) -> None:
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
                        raise AppArchiveTooLarge("ZIP archive exceeds 256 MiB")
                    await asyncio.to_thread(archive.write, chunk)
            if received == 0:
                raise InvalidAppArchive("ZIP archive is empty")
            await asyncio.to_thread(self._extract_and_publish, public_id, archive_path)
        finally:
            if archive_path is not None:
                archive_path.unlink(missing_ok=True)

    def _extract_and_publish(self, public_id: str, archive_path: Path) -> None:
        project_dir = self.project_dir(public_id)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=project_dir))
        public = project_dir / "public"
        backup = project_dir / f".previous-{uuid.uuid4().hex}"
        try:
            self._extract(archive_path, staging)
            if not (staging / "index.html").is_file():
                raise InvalidAppArchive("ZIP archive must contain index.html at its root")
            if public.exists():
                public.rename(backup)
            try:
                staging.rename(public)
            except OSError:
                if backup.exists():
                    backup.rename(public)
                raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)
            if backup.exists() and public.exists():
                shutil.rmtree(backup)

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
        if asset.suffix.lower() not in {".html", ".js", ".css"}:
            return None
        if asset.stat().st_size > MAX_REWRITE_BYTES:
            return None
        content = asset.read_bytes()
        public = self.project_dir(public_id) / "public"
        prefix = f"/app/{public_id}/".encode()

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
