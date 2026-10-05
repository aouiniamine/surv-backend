from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from domains.projects.errors import InvalidProjectName, OrganizationUnavailable, ProjectNotFound
from domains.projects.model import Project

if TYPE_CHECKING:
    from domains.projects.repo import ProjectRepository


class ProjectService:
    def __init__(self, repo: ProjectRepository) -> None:
        self._repo = repo

    async def create(self, name: str, organization_id: UUID, user_id: UUID) -> Project:
        clean_name = name.strip()
        if not clean_name:
            raise InvalidProjectName("Project name must contain non-whitespace characters")
        if len(clean_name) > 120:
            raise InvalidProjectName("Project name must be at most 120 characters")
        project = await self._repo.create(clean_name, organization_id, user_id)
        if project is None:
            raise OrganizationUnavailable("Organization not found or inaccessible")
        return project

    async def get(self, project_id: UUID, user_id: UUID) -> Project:
        project = await self._repo.get(project_id, user_id)
        if project is None:
            raise ProjectNotFound("Project not found")
        return project

    async def list_for_user(self, user_id: UUID) -> list[Project]:
        return await self._repo.list_for_user(user_id)
