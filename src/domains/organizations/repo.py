from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from domains.organizations.model import Organization, OrganizationRole, UserOrganization
from generated.organizations.models import OrganizationRole as GeneratedRole
from generated.organizations.organizations import AsyncQuerier


class OrganizationRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(self, conn: AsyncConnection, name: str) -> Organization:
        row = await AsyncQuerier(conn).create_organization(name=name)
        if row is None:
            raise RuntimeError("INSERT organizations returned no row")
        return Organization(row.id, row.name, row.created_at)

    async def add_member(
        self, conn: AsyncConnection, user_id: UUID, organization_id: UUID, role: OrganizationRole
    ) -> None:
        await AsyncQuerier(conn).add_organization_member(
            user_id=user_id, organization_id=organization_id, role=GeneratedRole(role.value)
        )

    async def create_for_user(self, name: str, user_id: UUID) -> UserOrganization:
        async with self._engine.begin() as conn:
            organization = await self.create(conn, name)
            await self.add_member(conn, user_id, organization.id, OrganizationRole.ADMIN)
        return UserOrganization(
            organization.id, organization.name, OrganizationRole.ADMIN, organization.created_at
        )

    async def list_for_user(self, user_id: UUID) -> list[UserOrganization]:
        async with self._engine.connect() as conn:
            rows = [
                UserOrganization(row.id, row.name, OrganizationRole(row.role), row.created_at)
                async for row in AsyncQuerier(conn).list_user_organizations(user_id=user_id)
            ]
        return rows
