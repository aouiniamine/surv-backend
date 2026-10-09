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

    async def record_deployment(self, project_id, previous):
        self.deployments += 1
        return []


class Storage:
    def __init__(self):
        self.calls = 0

    async def deploy_archive(self, public_id, archive, record, *, backup_previous):
        self.calls += 1
        await record(None)


def test_publish_dev_app_is_idempotent_when_live_matches(tmp_path):
    project_id = uuid4()
    user_id = uuid4()
    project = Project(project_id, uuid4(), "Site", "abc1234", "DEPLOYED", datetime.now(UTC))
    repo = ProjectRepo(project)
    storage = Storage()
    service = ProjectService(repo, object(), storage)

    def archive(public_id):
        path = tmp_path / "dev.zip"
        path.write_bytes(b"zip")
        return path

    def matches_live(public_id):
        return storage.calls > 0

    async def has_active_run(project_id):
        return False

    async def scenario():
        async def active_run(project_id):
            return True

        with pytest.raises(ValueError, match="Agent run is active"):
            await service.publish_dev_app(
                project_id, user_id, active_run, matches_live, archive
            )
        assert storage.calls == 0
        assert await service.publish_dev_app(
            project_id, user_id, has_active_run, matches_live, archive
        ) == "abc1234"
        assert await service.publish_dev_app(
            project_id, user_id, has_active_run, matches_live, archive
        ) == "abc1234"
        assert storage.calls == 1
        assert repo.deployments == 1

    asyncio.run(scenario())
