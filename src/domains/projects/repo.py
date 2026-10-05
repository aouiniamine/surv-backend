import secrets
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from domains.organizations.model import OrganizationRole
from domains.projects.model import Project
from generated.projects.projects import AsyncQuerier

PUBLIC_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
PUBLIC_ID_ATTEMPTS = 5


class ProjectRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(self, name: str, organization_id: UUID, user_id: UUID) -> Project | None:
        async with self._engine.begin() as conn:
            querier = AsyncQuerier(conn)
            for _ in range(PUBLIC_ID_ATTEMPTS):
                public_id = "".join(secrets.choice(PUBLIC_ID_ALPHABET) for _ in range(7))
                row = await querier.create_project(
                    name=name, organization_id=organization_id, user_id=user_id,
                    public_id=public_id,
                )
                if row is not None:
                    return Project(
                        id=row.id,
                        organization_id=row.organization_id,
                        name=row.name,
                        public_id=row.public_id,
                        created_at=row.created_at,
                    )
        return None

    async def get(
        self, project_id: UUID, user_id: UUID
    ) -> tuple[Project, OrganizationRole | None] | None:
        async with self._engine.connect() as conn:
            row = await AsyncQuerier(conn).get_project(id=project_id, user_id=user_id)
        if row is None:
            return None
        project = Project(
            id=row.id,
            organization_id=row.organization_id,
            name=row.name,
            public_id=row.public_id,
            created_at=row.created_at,
        )
        return project, OrganizationRole(row.role) if row.role is not None else None

    async def list_for_organization(self, organization_id: UUID, user_id: UUID) -> list[Project]:
        async with self._engine.connect() as conn:
            return [
                Project(row.id, row.organization_id, row.name, row.public_id, row.created_at)
                async for row in AsyncQuerier(conn).list_organization_projects(
                    organization_id=organization_id, user_id=user_id
                )
            ]

    async def list_for_user(self, user_id: UUID) -> list[Project]:
        async with self._engine.connect() as conn:
            return [
                Project(row.id, row.organization_id, row.name, row.public_id, row.created_at)
                async for row in AsyncQuerier(conn).list_projects_for_user(user_id=user_id)
            ]
