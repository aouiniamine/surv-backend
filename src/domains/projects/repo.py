from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from domains.projects.model import Project
from generated.projects.projects import AsyncQuerier


class ProjectRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(self, name: str, organization_id: UUID, user_id: UUID) -> Project | None:
        async with self._engine.begin() as conn:
            row = await AsyncQuerier(conn).create_project(
                name=name, organization_id=organization_id, user_id=user_id
            )
        if row is None:
            return None
        return Project(
            id=row.id,
            organization_id=row.organization_id,
            name=row.name,
            created_at=row.created_at,
        )

    async def get(self, project_id: UUID, user_id: UUID) -> Project | None:
        async with self._engine.connect() as conn:
            row = await AsyncQuerier(conn).get_project(id=project_id, user_id=user_id)
        if row is None:
            return None
        return Project(
            id=row.id,
            organization_id=row.organization_id,
            name=row.name,
            created_at=row.created_at,
        )
