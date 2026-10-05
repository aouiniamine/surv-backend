from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from domains.auth.errors import AccountExists
from domains.auth.model import Registration
from domains.organizations.model import OrganizationRole
from domains.organizations.repo import OrganizationRepository
from domains.users.model import User
from domains.users.repo import UserRepository


class AuthRepository:
    def __init__(
        self, engine: AsyncEngine, users: UserRepository, organizations: OrganizationRepository
    ) -> None:
        self._engine = engine
        self._users = users
        self._organizations = organizations

    async def get_user_by_email(self, email: str) -> User | None:
        return await self._users.get_by_email(email)

    async def get_user_by_id(self, user_id: UUID) -> User | None:
        return await self._users.get_by_id(user_id)

    async def register(
        self, email: str, first_name: str, last_name: str, organization_name: str
    ) -> Registration:
        try:
            async with self._engine.begin() as conn:
                user = await self._users.create(conn, email, first_name, last_name)
                organization = await self._organizations.create(conn, organization_name)
                await self._organizations.add_member(
                    conn, user.id, organization.id, OrganizationRole.ADMIN
                )
        except IntegrityError as exc:
            if await self._users.get_by_email(email) is not None:
                raise AccountExists("You already have an account") from exc
            raise
        return Registration(user=user, organization=organization)
