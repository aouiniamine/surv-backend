import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from domains.organizations.model import OrganizationRole
from domains.projects.model import Project
from domains.projects.service import ProjectService


class ProjectRepo:
    def __init__(self, project):
        self.project = project
        self.deployments = 0

    async def get(self, project_id, user_id):
        return self.project, OrganizationRole.ADMIN

    async def get_deployable(self, public_id, user_id):
        return self.project

    @asynccontextmanager
    async def project_lock(self, project_id):
        yield

    async def record_deployment(self, project_id, previous, after_record):
        self.deployments += 1
        await after_record(None)
        return []


class Storage:
    def __init__(self):
        self.calls = 0

    async def deploy_archive(self, public_id, archive, record, *, backup_previous):
        self.calls += 1
        await record(None)


def test_publish_checks_revision_and_retries_are_idempotent(tmp_path):
    project_id = uuid4()
    user_id = uuid4()
    project = Project(project_id, uuid4(), "Site", "abc1234", "DEPLOYED", datetime.now(UTC))
    repo = ProjectRepo(project)
    storage = Storage()
    service = ProjectService(repo, object(), storage)
    revision = "a" * 64
    published = False

    async def current():
        return revision, published

    async def mark(conn):
        nonlocal published
        published = True

    def archive(public_id):
        path = tmp_path / "draft.zip"
        path.write_bytes(b"zip")
        return path

    async def scenario():
        assert await service.publish_draft(
            project_id, user_id, revision, current, lambda public_id: revision, archive, mark
        ) == "abc1234"
        assert await service.publish_draft(
            project_id, user_id, revision, current, lambda public_id: revision, archive, mark
        ) == "abc1234"
        assert storage.calls == 1
        assert repo.deployments == 1
        with pytest.raises(ValueError, match="Draft files changed"):
            await service.publish_draft(
                project_id, user_id, revision, current, lambda public_id: "b" * 64,
                archive, mark,
            )

    asyncio.run(scenario())
