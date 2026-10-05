from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from domains.organizations.errors import OrganizationNotFound
from domains.organizations.model import ROLE_PRIORITY, OrganizationRole
from domains.organizations.service import OrganizationService
from domains.projects.errors import (
    InvalidProjectName,
    OrganizationUnavailable,
    ProjectAccessDenied,
    ProjectNotFound,
)
from domains.projects.model import Project

if TYPE_CHECKING:
    from domains.projects.repo import ProjectRepository


class ProjectService:
    def __init__(self, repo: ProjectRepository, organizations: OrganizationService) -> None:
        self._repo = repo
        self._organizations = organizations

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
        return project

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
