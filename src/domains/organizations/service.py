from uuid import UUID

from domains.organizations.model import UserOrganization
from domains.organizations.repo import OrganizationRepository


class OrganizationService:
    def __init__(self, repo: OrganizationRepository) -> None:
        self._repo = repo

    async def list_for_user(self, user_id: UUID) -> list[UserOrganization]:
        return await self._repo.list_for_user(user_id)
