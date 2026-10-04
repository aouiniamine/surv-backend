from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from domains.users.model import User
from generated.users.users import AsyncQuerier


class UserRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(
        self, conn: AsyncConnection, email: str, first_name: str, last_name: str
    ) -> User:
        row = await AsyncQuerier(conn).create_user(
            email=email, first_name=first_name, last_name=last_name
        )
        if row is None:
            raise RuntimeError("INSERT users returned no row")
        return User(row.id, row.email, row.first_name, row.last_name, row.created_at)

    async def get_by_email(self, email: str) -> User | None:
        async with self._engine.connect() as conn:
            row = await AsyncQuerier(conn).get_user_by_email(email=email)
        if row is None:
            return None
        return User(row.id, row.email, row.first_name, row.last_name, row.created_at)

    async def get_by_id(self, user_id: UUID) -> User | None:
        async with self._engine.connect() as conn:
            row = await AsyncQuerier(conn).get_user_by_id(id=user_id)
        if row is None:
            return None
        return User(row.id, row.email, row.first_name, row.last_name, row.created_at)
