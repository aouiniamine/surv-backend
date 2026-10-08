from uuid import uuid4

import pytest

from domains.agent.errors import AgentInvalid
from domains.agent.workspace import AgentWorkspace
from domains.projects.storage import ProjectStorage


def test_candidate_commit_diff_and_rollback(tmp_path):
    workspace = AgentWorkspace(ProjectStorage(tmp_path))
    public_id = "abc1234"
    live = tmp_path / public_id / "public"
    source = tmp_path / public_id / "dev" / "workspace"
    live.mkdir(parents=True)
    source.mkdir(parents=True)
    (live / "index.html").write_text("live", encoding="utf-8")
    (source / "index.html").write_text("old draft", encoding="utf-8")
    first = uuid4()
    workspace.prepare(public_id, first)
    workspace.write_file(public_id, first, "index.html", "new draft")
    workspace.write_file(public_id, first, "style.css", "body { color: red; }")
    changes = workspace.changes(public_id, first)
    assert [item[:2] for item in changes] == [
        ("index.html", "modified"),
        ("style.css", "added"),
    ]
    revision = workspace.commit(public_id, first)
    assert revision == workspace.revision(public_id)
    assert (live / "index.html").read_text() == "live"
    workspace.rollback(public_id, first)
    assert (source / "index.html").read_text() == "old draft"
    assert not (tmp_path / public_id / "dev" / "public").exists()


@pytest.mark.parametrize(
    "name", ["../index.html", "/index.html", "other/../../index.html", ".secret.txt", "a\\b.js"]
)
def test_file_tool_rejects_escape(tmp_path, name):
    workspace = AgentWorkspace(ProjectStorage(tmp_path))
    run_id = uuid4()
    workspace.prepare("abc1234", run_id)
    with pytest.raises(AgentInvalid):
        workspace.write_file("abc1234", run_id, name, "x")


def test_candidate_rejects_symlink(tmp_path):
    workspace = AgentWorkspace(ProjectStorage(tmp_path))
    run_id = uuid4()
    candidate = workspace.prepare("abc1234", run_id)
    (candidate / "outside.html").symlink_to(tmp_path / "outside.html")
    with pytest.raises(AgentInvalid):
        workspace.list_files("abc1234", run_id)


def test_reconcile_restores_draft_after_crash_before_database_commit(tmp_path):
    workspace = AgentWorkspace(ProjectStorage(tmp_path))
    public_id = "abc1234"
    source = tmp_path / public_id / "dev" / "workspace"
    source.mkdir(parents=True)
    (source / "index.html").write_text("before", encoding="utf-8")
    first = uuid4()
    workspace.prepare(public_id, first)
    workspace.write_file(public_id, first, "index.html", "after")
    workspace.commit(public_id, first)
    workspace.reconcile(public_id, None)
    assert (source / "index.html").read_text() == "before"
    assert not (tmp_path / public_id / "dev" / "public").exists()
