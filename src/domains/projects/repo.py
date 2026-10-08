import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from domains.organizations.model import OrganizationRole
from domains.projects.model import Project, ProjectBackup
from generated.agent.agent import AsyncQuerier as AgentQuerier
from generated.projects.projects import AsyncQuerier

PUBLIC_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
PUBLIC_ID_ATTEMPTS = 5


class ProjectRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @asynccontextmanager
    async def project_lock(self, project_id: UUID) -> AsyncIterator[None]:
        async with self._engine.connect() as conn:
            querier = AsyncQuerier(conn)
            await querier.acquire_project_lock(project_id=str(project_id))
            try:
                yield
            finally:
                await querier.release_project_lock(project_id=str(project_id))

    async def create(self, name: str, organization_id: UUID, user_id: UUID) -> Project | None:
        async with self._engine.begin() as conn:
            querier = AsyncQuerier(conn)
            for _ in range(PUBLIC_ID_ATTEMPTS):
                public_id = "".join(secrets.choice(PUBLIC_ID_ALPHABET) for _ in range(7))
                row = await querier.create_project(
                    name=name,
                    organization_id=organization_id,
                    user_id=user_id,
                    public_id=public_id,
                )
                if row is not None:
                    return Project(
                        id=row.id,
                        organization_id=row.organization_id,
                        name=row.name,
                        public_id=row.public_id,
                        status=row.status,
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
            status=row.status,
            created_at=row.created_at,
        )
        return project, OrganizationRole(row.role) if row.role is not None else None

    async def get_deployable(self, public_id: str, user_id: UUID) -> Project | None:
        async with self._engine.connect() as conn:
            row = await AsyncQuerier(conn).get_deployable_project(
                public_id=public_id, user_id=user_id
            )
        if row is None:
            return None
        return Project(
            row.id, row.organization_id, row.name, row.public_id, row.status, row.created_at
        )

    async def list_for_organization(self, organization_id: UUID, user_id: UUID) -> list[Project]:
        async with self._engine.connect() as conn:
            return [
                Project(
                    row.id, row.organization_id, row.name, row.public_id, row.status, row.created_at
                )
                async for row in AsyncQuerier(conn).list_organization_projects(
                    organization_id=organization_id, user_id=user_id
                )
            ]

    async def list_for_user(self, user_id: UUID) -> list[Project]:
        async with self._engine.connect() as conn:
            return [
                Project(
                    row.id, row.organization_id, row.name, row.public_id, row.status, row.created_at
                )
                async for row in AsyncQuerier(conn).list_projects_for_user(user_id=user_id)
            ]

    async def record_deployment(
        self, project_id: UUID, previous_archive_path: str | None,
        after_record: Callable[[AsyncConnection], Awaitable[None]] | None = None,
    ) -> list[str]:
        async with self._engine.begin() as conn:
            querier = AsyncQuerier(conn)
            expired_paths: list[str] = []
            if previous_archive_path is not None:
                await querier.create_project_backup(
                    project_id=project_id, archive_path=previous_archive_path
                )
                expired_paths = [
                    path async for path in querier.trim_project_backups(project_id=project_id)
                ]
            await querier.mark_project_deployed(project_id=project_id)
            if after_record is not None:
                await after_record(conn)
            else:
                await AgentQuerier(conn).clear_agent_draft_publication(project_id=project_id)
        return expired_paths

    async def list_backups(self, project_id: UUID, user_id: UUID) -> list[ProjectBackup]:
        async with self._engine.connect() as conn:
            return [
                ProjectBackup(row.id, row.archive_path, row.created_at)
                async for row in AsyncQuerier(conn).list_project_backups(
                    project_id=project_id, user_id=user_id
                )
            ]

    async def get_backup(self, project_id: UUID, backup_id: UUID) -> ProjectBackup | None:
        async with self._engine.connect() as conn:
            row = await AsyncQuerier(conn).get_project_backup(
                project_id=project_id, backup_id=backup_id
            )
        return ProjectBackup(row.id, row.archive_path, row.created_at) if row else None
