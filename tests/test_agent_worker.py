import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

from domains.agent.model import AgentProject, ClaimedRun
from domains.agent.providers.base import ModelReply, ToolCall
from domains.agent.worker import AgentWorker
from domains.agent.workspace import AgentWorkspace
from domains.organizations.model import OrganizationRole
from domains.projects.storage import ProjectStorage


class FakeRepo:
    def __init__(self, run, project):
        self.run = run
        self.project = project
        self.claimed = False
        self.status = "running"
        self.events = []
        self.changes = []
        self.revision = None
        self.events_before_completion = []

    async def claim_run(self):
        if self.claimed:
            return None
        self.claimed = True
        return self.run

    async def get_project(self, project_id, user_id):
        return self.project

    async def run_status(self, run_id):
        return self.status

    async def add_event(self, run_id, kind, content):
        self.events.append((kind, content))

    async def add_change(self, run_id, path, kind, diff):
        self.changes.append((path, kind, diff))

    async def complete_run(self, run_id, revision):
        self.events_before_completion = list(self.events)
        self.revision = revision
        self.status = "succeeded"
        return True

    async def heartbeat(self, run_id):
        pass


class FakeProjectRepo:
    @asynccontextmanager
    async def project_lock(self, project_id):
        yield


class FakeProvider:
    model_id = "test-model"
    provider_id = "fake"

    def __init__(self):
        self.calls = 0

    async def check_ready(self):
        pass

    async def complete(self, messages, tools, on_text):
        self.calls += 1
        if self.calls == 1:
            progress = "I’m updating the page so visitors can find the key information faster."
            await on_text(progress)
            call = ToolCall("write_file", {"path": "index.html", "content": "<h1>Dev app</h1>"})
            return ModelReply(progress, (call,), {"role": "assistant", "content": progress})
        summary = "Customers can now review the new landing page.\n```html\n<h1>Dev app</h1>\n```"
        await on_text(summary)
        return ModelReply(summary, (), {"role": "assistant", "content": summary})


def test_worker_writes_dev_public_and_records_review_artifacts(tmp_path):
    project_id = uuid4()
    run = ClaimedRun(uuid4(), project_id, uuid4(), "Build a page", "fake", "test-model", None, None)
    project = AgentProject(project_id, "abc1234", "DEPLOYED", OrganizationRole.ADMIN)
    repository = FakeRepo(run, project)
    worker = AgentWorker(
        repository,
        AgentWorkspace(ProjectStorage(tmp_path)),
        FakeProvider(),
        FakeProjectRepo(),
    )
    assert asyncio.run(worker.run_once()) is True
    assert repository.status == "succeeded"
    assert repository.revision is not None
    assert repository.changes[0][:2] == ("index.html", "added")
    assert any(kind == "text" and "visitors can find" in content
               for kind, content in repository.events_before_completion)
    assert any(kind == "status" and content == "Applying changes to the app."
               for kind, content in repository.events_before_completion)
    assert not any("index.html" in content for _, content in repository.events)
    assert any(
        kind == "text" and content == "Customers can now review the new landing page."
        for kind, content in repository.events
    )
    page = tmp_path / "abc1234" / "dev" / "public" / "index.html"
    assert page.read_text() == "<h1>Dev app</h1>"
    assert not (tmp_path / "abc1234" / "public").exists()
