from uuid import UUID

from fastapi import APIRouter, Depends

from core.dependencies import current_user_id, get_organization_service
from domains.organizations.dto import OrganizationResponse
from domains.organizations.service import OrganizationService

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.get("", response_model=list[OrganizationResponse])
async def list_organizations(
    user_id: UUID = Depends(current_user_id),
    service: OrganizationService = Depends(get_organization_service),
) -> list[OrganizationResponse]:
    return [
        OrganizationResponse.model_validate(item)
        for item in await service.list_for_user(user_id)
    ]
