import difflib
import hashlib
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from domains.agent.errors import AgentInvalid
from domains.projects.storage import (
    MAX_ARCHIVE_BYTES,
    MAX_ENTRIES,
    MAX_EXTRACTED_BYTES,
    ProjectStorage,
)

MAX_FILE_BYTES = 256 * 1024
MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_DIFF_CHARS = 30_000
ALLOWED_SUFFIXES = {".html", ".css", ".js", ".json", ".svg", ".txt", ".md"}


def _safe_relative(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or any(part in ("", ".", "..") or part.startswith(".") for part in name.split("/"))
        or len(name) > 512
        or path.suffix.lower() not in ALLOWED_SUFFIXES
    ):
        raise AgentInvalid("Invalid dev file path")
    return path


def _assets(root: Path) -> dict[str, Path]:
    if not root.exists():
        return {}
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise AgentInvalid("Dev files root is not a directory")
    result: dict[str, Path] = {}
    total = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            target = Path(directory) / name
            if not stat.S_ISDIR(target.lstat().st_mode):
                raise AgentInvalid("Dev files contain a link or special directory")
        for name in files:
            target = Path(directory) / name
            if not stat.S_ISREG(target.lstat().st_mode) or target.stat().st_nlink != 1:
                raise AgentInvalid("Dev files contain a link or special file")
            relative = target.relative_to(root).as_posix()
            total += target.stat().st_size
            if total > MAX_EXTRACTED_BYTES or len(result) >= MAX_ENTRIES:
                raise AgentInvalid("Dev files exceed deployment limits")
            result[relative] = target
    return result


def _files(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    total = 0
    for name, path in sorted(
        _assets(root).items(), key=lambda item: (item[0] != "index.html", item[0])
    ):
        try:
            _safe_relative(name)
        except AgentInvalid:
            continue
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            continue
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if total + size > MAX_SOURCE_BYTES:
            continue
        total += size
        result[name] = path
    return result


def _same_content(first: Path, second: Path) -> bool:
    if first.stat().st_size != second.stat().st_size:
        return False
    with first.open("rb") as before, second.open("rb") as after:
        while chunk := before.read(1024 * 1024):
            if chunk != after.read(len(chunk)):
                return False
    return True


class AgentWorkspace:
    def __init__(self, storage: ProjectStorage) -> None:
        self._storage = storage

    def _dev(self, public_id: str) -> Path:
        return self._storage.project_dir(public_id) / "dev"

    def _public(self, public_id: str) -> Path:
        return self._dev(public_id) / "public"

    def prepare(self, public_id: str, run_id: UUID) -> Path:
        dev = self._dev(public_id)
        public = dev / "public"
        _assets(public)
        snapshot = dev / ".runs" / str(run_id) / "baseline"
        if snapshot.exists():
            raise AgentInvalid("Run baseline already exists")
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        if public.is_dir():
            shutil.copytree(public, snapshot)
        else:
            snapshot.mkdir()
            public.mkdir(parents=True)
        return public

    def copy_live_app_to_dev(self, public_id: str) -> None:
        live = self._storage.project_dir(public_id) / "public"
        if "index.html" not in _assets(live):
            raise AgentInvalid("No deployed application to copy")
        dev = self._dev(public_id)
        dev.mkdir(parents=True, exist_ok=True)
        public = dev / "public"
        with tempfile.TemporaryDirectory(prefix=".copy-live-", dir=dev) as temporary:
            stage = Path(temporary)
            shutil.copytree(live, stage / "public")
            previous = stage / "previous"
            if public.exists():
                public.rename(previous)
            try:
                (stage / "public").rename(public)
            except BaseException:
                if previous.exists():
                    previous.rename(public)
                raise

    def list_files(self, public_id: str) -> list[str]:
        return sorted(_files(self._public(public_id)))

    def read_file(self, public_id: str, name: str) -> str:
        relative = _safe_relative(name)
        files = _files(self._public(public_id))
        path = files.get(relative.as_posix())
        if path is None:
            raise AgentInvalid("File not found")
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise AgentInvalid("File is not UTF-8 text") from exc

    def write_file(self, public_id: str, name: str, content: str) -> str:
        relative = _safe_relative(name)
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            raise AgentInvalid("File exceeds 256 KiB")
        public = self._public(public_id)
        existing = _files(public)
        if relative.as_posix() in _assets(public) and relative.as_posix() not in existing:
            raise AgentInvalid("File cannot be edited by the agent")
        old_file = existing.get(relative.as_posix())
        old_size = old_file.stat().st_size if old_file else 0
        total = sum(path.stat().st_size for path in existing.values()) - old_size + len(encoded)
        if total > MAX_SOURCE_BYTES:
            raise AgentInvalid("Dev files exceed 10 MiB of editable source")
        if relative.as_posix() not in existing and len(existing) >= MAX_ENTRIES:
            raise AgentInvalid("Dev files have too many entries")
        target = public.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        with temporary.open("xb") as output:
            output.write(encoded)
        temporary.replace(target)
        return f"Wrote {relative.as_posix()} ({len(encoded)} bytes)"

    def delete_file(self, public_id: str, name: str) -> str:
        relative = _safe_relative(name)
        files = _files(self._public(public_id))
        path = files.get(relative.as_posix())
        if path is None:
            raise AgentInvalid("File not found")
        path.unlink()
        return f"Deleted {relative.as_posix()}"

    def changes(self, public_id: str, run_id: UUID) -> list[tuple[str, str, str | None]]:
        dev = self._dev(public_id)
        baseline = _assets(dev / ".runs" / str(run_id) / "baseline")
        current = _assets(self._public(public_id))
        changes: list[tuple[str, str, str | None]] = []
        for name in sorted(baseline.keys() | current.keys()):
            if name in baseline and name in current and _same_content(
                baseline[name], current[name]
            ):
                continue
            if name not in baseline:
                kind = "added"
            elif name not in current:
                kind = "deleted"
            else:
                kind = "modified"
            diff = None
            if (
                name.endswith(tuple(ALLOWED_SUFFIXES))
                and (name not in baseline or baseline[name].stat().st_size <= MAX_FILE_BYTES)
                and (name not in current or current[name].stat().st_size <= MAX_FILE_BYTES)
            ):
                before = baseline[name].read_bytes() if name in baseline else b""
                after = current[name].read_bytes() if name in current else b""
                try:
                    diff = "".join(difflib.unified_diff(
                        before.decode("utf-8").splitlines(keepends=True),
                        after.decode("utf-8").splitlines(keepends=True),
                        fromfile=f"a/{name}", tofile=f"b/{name}",
                    ))[:MAX_DIFF_CHARS]
                except UnicodeDecodeError:
                    pass
            changes.append((name, kind, diff))
        return changes

    def revision(self, public_id: str) -> str:
        files = _assets(self._public(public_id))
        if "index.html" not in files:
            raise AgentInvalid("No dev app to publish")
        return self._digest(files)

    @staticmethod
    def _digest(files: dict[str, Path]) -> str:
        digest = hashlib.sha256()
        for name, path in sorted(files.items()):
            digest.update(name.encode() + b"\0")
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
            digest.update(b"\0")
        return digest.hexdigest()

    def cleanup(self, public_id: str, run_id: UUID) -> None:
        run_root = self._dev(public_id) / ".runs" / str(run_id)
        if run_root.exists():
            shutil.rmtree(run_root)

    def archive(self, public_id: str) -> Path:
        public = self._public(public_id)
        files = _assets(public)
        if "index.html" not in files:
            raise AgentInvalid("No dev app to publish")
        archive = self._dev(public_id) / ".runs" / f"publish-{uuid4()}.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
            for name, path in sorted(files.items()):
                output.write(path, name)
        if archive.stat().st_size > MAX_ARCHIVE_BYTES:
            archive.unlink(missing_ok=True)
            raise AgentInvalid("Dev app ZIP exceeds 20 MiB")
        return archive

    def matches_live(self, public_id: str) -> bool:
        current = _assets(self._public(public_id))
        live = _assets(self._storage.project_dir(public_id) / "public")
        return (
            "index.html" in current
            and "index.html" in live
            and self._digest(current) == self._digest(live)
        )
