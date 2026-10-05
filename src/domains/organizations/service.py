from uuid import UUID

from domains.organizations.errors import (
    InvalidOrganizationName,
    OrganizationAccessDenied,
    OrganizationNotFound,
)
from domains.organizations.model import ROLE_PRIORITY, OrganizationRole, UserOrganization
from domains.organizations.repo import OrganizationRepository


class OrganizationService:
    def __init__(self, repo: OrganizationRepository) -> None:
        self._repo = repo

    async def list_for_user(self, user_id: UUID) -> list[UserOrganization]:
        return await self._repo.list_for_user(user_id)

    async def require_access(
        self, organization_id: UUID, user_id: UUID, minimum_role: OrganizationRole
    ) -> UserOrganization:
        access = await self._repo.get_access(organization_id, user_id)
        if access is None:
            raise OrganizationNotFound("Organization not found")
        organization, role = access
        if role is None or ROLE_PRIORITY[role] < ROLE_PRIORITY[minimum_role]:
            raise OrganizationAccessDenied("Insufficient organization access")
        return UserOrganization(organization.id, organization.name, role, organization.created_at)

    async def create_for_user(self, name: str, user_id: UUID) -> UserOrganization:
        clean_name = name.strip()
        if not clean_name:
            raise InvalidOrganizationName(
                "Organization name must contain non-whitespace characters"
            )
        if len(clean_name) > 150:
            raise InvalidOrganizationName("Organization name must be at most 150 characters")
        return await self._repo.create_for_user(clean_name, user_id)
