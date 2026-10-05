from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from core.dependencies import current_user_id, get_organization_service
from core.responses import ApiResponse, success
from domains.organizations.dto import CreateOrganizationRequest, OrganizationResponse
from domains.organizations.errors import (
    InvalidOrganizationName,
    OrganizationAccessDenied,
    OrganizationNotFound,
)
from domains.organizations.model import OrganizationRole
from domains.organizations.service import OrganizationService

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.post(
    "", response_model=ApiResponse[OrganizationResponse], status_code=status.HTTP_201_CREATED
)
async def create_organization(
    payload: CreateOrganizationRequest,
    user_id: UUID = Depends(current_user_id),
    service: OrganizationService = Depends(get_organization_service),
) -> ApiResponse[OrganizationResponse]:
    try:
        organization = await service.create_for_user(payload.name, user_id)
    except InvalidOrganizationName as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return success(OrganizationResponse.model_validate(organization), "Organization created", 201)


@router.get("", response_model=ApiResponse[list[OrganizationResponse]])
async def list_organizations(
    user_id: UUID = Depends(current_user_id),
    service: OrganizationService = Depends(get_organization_service),
) -> ApiResponse[list[OrganizationResponse]]:
    data = [
        OrganizationResponse.model_validate(item) for item in await service.list_for_user(user_id)
    ]
    return success(data, "Organizations loaded")


@router.get("/{organization_id}", response_model=ApiResponse[OrganizationResponse])
async def get_organization(
    organization_id: UUID,
    user_id: UUID = Depends(current_user_id),
    service: OrganizationService = Depends(get_organization_service),
) -> ApiResponse[OrganizationResponse]:
    try:
        organization = await service.require_access(organization_id, user_id, OrganizationRole.QA)
    except OrganizationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OrganizationAccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return success(OrganizationResponse.model_validate(organization), "Organization loaded")
