import difflib
import hashlib
import os
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath
from uuid import UUID

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
        raise AgentInvalid("Invalid workspace file path")
    return path


def _files(root: Path) -> dict[str, Path]:
    if not root.exists():
        return {}
    result: dict[str, Path] = {}
    total = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            target = Path(directory) / name
            if not stat.S_ISDIR(target.lstat().st_mode):
                raise AgentInvalid("Workspace contains a link or special directory")
        for name in files:
            target = Path(directory) / name
            if not stat.S_ISREG(target.lstat().st_mode) or target.stat().st_nlink != 1:
                raise AgentInvalid("Workspace contains a link or special file")
            relative = target.relative_to(root).as_posix()
            _safe_relative(relative)
            if target.stat().st_size > MAX_FILE_BYTES:
                raise AgentInvalid("Workspace file exceeds 256 KiB")
            total += target.stat().st_size
            if total > MAX_SOURCE_BYTES or len(result) >= MAX_ENTRIES:
                raise AgentInvalid("Workspace exceeds its file limit")
            result[relative] = target
    return result


class AgentWorkspace:
    def __init__(self, storage: ProjectStorage) -> None:
        self._storage = storage

    def _dev(self, public_id: str) -> Path:
        return self._storage.project_dir(public_id) / "dev"

    def candidate(self, public_id: str, run_id: UUID) -> Path:
        return self._dev(public_id) / ".runs" / str(run_id) / "workspace"

    def prepare(self, public_id: str, run_id: UUID) -> Path:
        dev = self._dev(public_id)
        baseline = dev / "workspace"
        _files(baseline)
        candidate = self.candidate(public_id, run_id)
        if candidate.exists():
            raise AgentInvalid("Run workspace already exists")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        if baseline.is_dir():
            shutil.copytree(baseline, candidate)
        else:
            candidate.mkdir()
        return candidate

    def list_files(self, public_id: str, run_id: UUID) -> list[str]:
        return sorted(_files(self.candidate(public_id, run_id)))

    def read_file(self, public_id: str, run_id: UUID, name: str) -> str:
        relative = _safe_relative(name)
        candidate = self.candidate(public_id, run_id)
        files = _files(candidate)
        path = files.get(relative.as_posix())
        if path is None:
            raise AgentInvalid("File not found")
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise AgentInvalid("File is not UTF-8 text") from exc

    def write_file(self, public_id: str, run_id: UUID, name: str, content: str) -> str:
        relative = _safe_relative(name)
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            raise AgentInvalid("File exceeds 256 KiB")
        candidate = self.candidate(public_id, run_id)
        existing = _files(candidate)
        old_file = existing.get(relative.as_posix())
        old_size = old_file.stat().st_size if old_file else 0
        total = sum(path.stat().st_size for path in existing.values()) - old_size + len(encoded)
        if total > MAX_SOURCE_BYTES:
            raise AgentInvalid("Workspace exceeds 10 MiB")
        if relative.as_posix() not in existing and len(existing) >= MAX_ENTRIES:
            raise AgentInvalid("Workspace has too many files")
        target = candidate.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        with temporary.open("xb") as output:
            output.write(encoded)
        temporary.replace(target)
        return f"Wrote {relative.as_posix()} ({len(encoded)} bytes)"

    def delete_file(self, public_id: str, run_id: UUID, name: str) -> str:
        relative = _safe_relative(name)
        files = _files(self.candidate(public_id, run_id))
        path = files.get(relative.as_posix())
        if path is None:
            raise AgentInvalid("File not found")
        path.unlink()
        return f"Deleted {relative.as_posix()}"

    def changes(self, public_id: str, run_id: UUID) -> list[tuple[str, str, str | None]]:
        baseline = _files(self._dev(public_id) / "workspace")
        candidate = _files(self.candidate(public_id, run_id))
        changes: list[tuple[str, str, str | None]] = []
        for name in sorted(baseline.keys() | candidate.keys()):
            before = baseline[name].read_bytes() if name in baseline else b""
            after = candidate[name].read_bytes() if name in candidate else b""
            if name in baseline and name in candidate and before == after:
                continue
            if name not in baseline:
                kind = "added"
            elif name not in candidate:
                kind = "deleted"
            else:
                kind = "modified"
            try:
                old_text = before.decode("utf-8").splitlines(keepends=True)
                new_text = after.decode("utf-8").splitlines(keepends=True)
                diff = "".join(
                    difflib.unified_diff(
                        old_text, new_text, fromfile=f"a/{name}", tofile=f"b/{name}"
                    )
                )[:MAX_DIFF_CHARS]
            except UnicodeDecodeError:
                diff = None
            changes.append((name, kind, diff))
        return changes

    def revision(self, public_id: str) -> str:
        files = _files(self._dev(public_id) / "public")
        if "index.html" not in files:
            raise AgentInvalid("No valid draft to publish")
        return self._digest(files)

    @staticmethod
    def _digest(files: dict[str, Path]) -> str:
        digest = hashlib.sha256()
        for name, path in sorted(files.items()):
            digest.update(name.encode() + b"\0" + path.read_bytes() + b"\0")
        return digest.hexdigest()

    def commit(self, public_id: str, run_id: UUID) -> str:
        dev = self._dev(public_id)
        candidate = self.candidate(public_id, run_id)
        files = _files(candidate)
        if "index.html" not in files:
            raise AgentInvalid("Draft must contain index.html")
        total_bytes = sum(path.stat().st_size for path in files.values())
        if len(files) > MAX_ENTRIES or total_bytes > MAX_EXTRACTED_BYTES:
            raise AgentInvalid("Draft exceeds deployment limits")
        revision = self._digest(files)
        run_root = candidate.parent
        staged_public = run_root / "public"
        shutil.copytree(candidate, staged_public)
        previous_workspace = run_root / "previous-workspace"
        previous_public = run_root / "previous-public"
        live_workspace = dev / "workspace"
        live_public = dev / "public"
        (run_root / "swap-revision").write_text(revision, encoding="ascii")
        try:
            if live_workspace.exists():
                live_workspace.rename(previous_workspace)
            if live_public.exists():
                live_public.rename(previous_public)
            candidate.rename(live_workspace)
            staged_public.rename(live_public)
        except BaseException:
            if live_workspace.exists():
                shutil.rmtree(live_workspace)
            if live_public.exists():
                shutil.rmtree(live_public)
            if previous_workspace.exists():
                previous_workspace.rename(live_workspace)
            if previous_public.exists():
                previous_public.rename(live_public)
            (run_root / "swap-revision").unlink(missing_ok=True)
            raise
        return revision

    def rollback(self, public_id: str, run_id: UUID) -> None:
        dev = self._dev(public_id)
        run_root = self.candidate(public_id, run_id).parent
        for name, previous in (("workspace", "previous-workspace"), ("public", "previous-public")):
            live = dev / name
            if live.exists():
                shutil.rmtree(live)
            old = run_root / previous
            if old.exists():
                old.rename(live)
        (run_root / "swap-revision").unlink(missing_ok=True)

    def finalize(self, public_id: str, run_id: UUID) -> None:
        self.cleanup(public_id, run_id)

    def cleanup(self, public_id: str, run_id: UUID) -> None:
        run_root = self.candidate(public_id, run_id).parent
        if run_root.exists() and not (run_root / "swap-revision").exists():
            shutil.rmtree(run_root)

    def reconcile(self, public_id: str, expected_revision: str | None) -> None:
        try:
            actual = self.revision(public_id)
        except AgentInvalid:
            actual = None
        if actual == expected_revision:
            return
        runs = self._dev(public_id) / ".runs"
        markers = sorted(
            runs.glob("*/swap-revision") if runs.exists() else (),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for marker in markers:
            if marker.read_text(encoding="ascii") != actual:
                continue
            try:
                run_id = UUID(marker.parent.name)
            except ValueError:
                continue
            self.rollback(public_id, run_id)
            try:
                restored = self.revision(public_id)
            except AgentInvalid:
                restored = None
            if restored == expected_revision:
                return
        raise AgentInvalid("Draft storage does not match its recorded revision")

    def archive(self, public_id: str, run_id: UUID) -> Path:
        public = self._dev(public_id) / "public"
        files = _files(public)
        if "index.html" not in files:
            raise AgentInvalid("No valid draft to publish")
        archive = self._dev(public_id) / ".runs" / f"publish-{run_id}.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
            for name, path in sorted(files.items()):
                output.write(path, name)
        if archive.stat().st_size > MAX_ARCHIVE_BYTES:
            archive.unlink(missing_ok=True)
            raise AgentInvalid("Draft ZIP exceeds 20 MiB")
        return archive
