from uuid import uuid4

import pytest

from domains.agent.errors import AgentInvalid
from domains.agent.workspace import AgentWorkspace
from domains.projects.storage import ProjectStorage


def test_agent_edits_dev_public_and_reports_changes(tmp_path):
    workspace = AgentWorkspace(ProjectStorage(tmp_path))
    public_id = "abc1234"
    live = tmp_path / public_id / "public"
    dev = tmp_path / public_id / "dev" / "public"
    live.mkdir(parents=True)
    dev.mkdir(parents=True)
    (live / "index.html").write_text("live", encoding="utf-8")
    (dev / "index.html").write_text("before", encoding="utf-8")
    run_id = uuid4()

    assert workspace.prepare(public_id, run_id) == dev
    workspace.write_file(public_id, "index.html", "after")
    workspace.write_file(public_id, "style.css", "body { color: red; }")

    assert (dev / "index.html").read_text() == "after"
    assert (live / "index.html").read_text() == "live"
    assert [item[:2] for item in workspace.changes(public_id, run_id)] == [
        ("index.html", "modified"), ("style.css", "added")
    ]
    workspace.cleanup(public_id, run_id)
    assert (dev / "index.html").read_text() == "after"
    assert not (tmp_path / public_id / "dev" / ".runs" / str(run_id)).exists()


@pytest.mark.parametrize(
    "name", ["../index.html", "/index.html", "other/../../index.html", ".secret.txt", "a\\b.js"]
)
def test_file_tool_rejects_escape(tmp_path, name):
    workspace = AgentWorkspace(ProjectStorage(tmp_path))
    workspace.prepare("abc1234", uuid4())
    with pytest.raises(AgentInvalid):
        workspace.write_file("abc1234", name, "x")


def test_dev_public_rejects_symlink(tmp_path):
    workspace = AgentWorkspace(ProjectStorage(tmp_path))
    dev = workspace.prepare("abc1234", uuid4())
    (dev / "outside.html").symlink_to(tmp_path / "outside.html")
    with pytest.raises(AgentInvalid):
        workspace.list_files("abc1234")


def test_copy_live_app_seeds_dev_public(tmp_path):
    workspace = AgentWorkspace(ProjectStorage(tmp_path))
    public_id = "abc1234"
    live = tmp_path / public_id / "public"
    live.mkdir(parents=True)
    (live / "index.html").write_text("live app", encoding="utf-8")
    (live / "logo.png").write_bytes(b"\x89PNG\x00")

    workspace.copy_live_app_to_dev(public_id)
    dev = tmp_path / public_id / "dev" / "public"
    assert (dev / "index.html").read_text() == "live app"
    assert (dev / "logo.png").read_bytes() == b"\x89PNG\x00"
    run_id = uuid4()
    workspace.prepare(public_id, run_id)
    assert workspace.list_files(public_id) == ["index.html"]
    workspace.write_file(public_id, "index.html", "edited")
    assert (dev / "index.html").read_text() == "edited"
    assert (live / "index.html").read_text() == "live app"
    assert not (tmp_path / public_id / "dev" / "workspace").exists()
