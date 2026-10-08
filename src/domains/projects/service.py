from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncConnection
from starlette.requests import Request

from domains.organizations.errors import OrganizationNotFound
from domains.organizations.model import ROLE_PRIORITY, OrganizationRole
from domains.organizations.service import OrganizationService
from domains.projects.errors import (
    InvalidProjectName,
    OrganizationUnavailable,
    ProjectAccessDenied,
    ProjectBackupNotFound,
    ProjectNotFound,
)
from domains.projects.model import Project, ProjectBackup
from domains.projects.storage import ProjectStorage

if TYPE_CHECKING:
    from domains.projects.repo import ProjectRepository


class ProjectService:
    def __init__(
        self, repo: ProjectRepository, organizations: OrganizationService, storage: ProjectStorage
    ) -> None:
        self._repo = repo
        self._organizations = organizations
        self.storage = storage

    async def create(self, name: str, organization_id: UUID, user_id: UUID) -> Project:
        clean_name = name.strip()
        if not clean_name:
            raise InvalidProjectName("Project name must contain non-whitespace characters")
        if len(clean_name) > 120:
            raise InvalidProjectName("Project name must be at most 120 characters")
        try:
            await self._organizations.require_access(
                organization_id, user_id, OrganizationRole.DEVELOPER
            )
        except OrganizationNotFound as exc:
            raise OrganizationUnavailable("Organization not found") from exc
        project = await self._repo.create(clean_name, organization_id, user_id)
        if project is None:
            raise ProjectAccessDenied("Insufficient organization access")
        self.storage.ensure_public(project.public_id)
        return project

    async def require_deployable(self, public_id: str, user_id: UUID) -> Project:
        project = await self._repo.get_deployable(public_id, user_id)
        if project is None:
            raise ProjectNotFound("Project not found or deployment access denied")
        return project

    async def deploy(self, public_id: str, user_id: UUID, request: Request) -> None:
        project = await self.require_deployable(public_id, user_id)
        async with self._repo.project_lock(project.id):
            project = await self.require_deployable(public_id, user_id)
            await self.storage.deploy(
                public_id,
                request,
                lambda previous: self._repo.record_deployment(project.id, previous),
                backup_previous=project.status == "DEPLOYED",
            )

    async def restore_backup(self, public_id: str, backup_id: UUID, user_id: UUID) -> None:
        project = await self.require_deployable(public_id, user_id)
        backup = await self._repo.get_backup(project.id, backup_id)
        if backup is None:
            raise ProjectBackupNotFound("Backup not found")
        async with self._repo.project_lock(project.id):
            project = await self.require_deployable(public_id, user_id)
            await self.storage.restore(
                public_id,
                backup.archive_path,
                lambda previous: self._repo.record_deployment(project.id, previous),
            )

    async def publish_archive(self, project_id: UUID, user_id: UUID, archive_path: Path) -> str:
        project = await self.get(project_id, user_id)
        async with self._repo.project_lock(project.id):
            project = await self.require_deployable(project.public_id, user_id)
            await self.storage.deploy_archive(
                project.public_id,
                archive_path,
                lambda previous: self._repo.record_deployment(project.id, previous),
                backup_previous=project.status == "DEPLOYED",
            )
        return project.public_id

    async def publish_draft(
        self,
        project_id: UUID,
        user_id: UUID,
        expected_revision: str,
        current_revision: Callable[[], Awaitable[tuple[str | None, bool]]],
        disk_revision: Callable[[str], str],
        make_archive: Callable[[str], Path],
        mark_published: Callable[[AsyncConnection], Awaitable[None]],
    ) -> str:
        project = await self.get(project_id, user_id)
        async with self._repo.project_lock(project.id):
            project = await self.require_deployable(project.public_id, user_id)
            actual_revision, already_published = await current_revision()
            if actual_revision != expected_revision:
                raise ValueError("Draft revision changed")
            if await asyncio.to_thread(disk_revision, project.public_id) != expected_revision:
                raise ValueError("Draft files changed")
            if already_published:
                return project.public_id
            archive = await asyncio.to_thread(make_archive, project.public_id)
            try:
                await self.storage.deploy_archive(
                    project.public_id,
                    archive,
                    lambda previous: self._repo.record_deployment(
                        project.id, previous, mark_published
                    ),
                    backup_previous=project.status == "DEPLOYED",
                )
            finally:
                archive.unlink(missing_ok=True)
        return project.public_id

    async def get(self, project_id: UUID, user_id: UUID) -> Project:
        access = await self._repo.get(project_id, user_id)
        if access is None:
            raise ProjectNotFound("Project not found")
        project, role = access
        if role is None or ROLE_PRIORITY[role] < ROLE_PRIORITY[OrganizationRole.QA]:
            raise ProjectAccessDenied("Insufficient project access")
        return project

    async def list_for_organization(self, organization_id: UUID, user_id: UUID) -> list[Project]:
        await self._organizations.require_access(organization_id, user_id, OrganizationRole.QA)
        return await self._repo.list_for_organization(organization_id, user_id)

    async def list_for_user(self, user_id: UUID) -> list[Project]:
        return await self._repo.list_for_user(user_id)

    async def list_backups(self, project_id: UUID, user_id: UUID) -> list[ProjectBackup]:
        return await self._repo.list_backups(project_id, user_id)
