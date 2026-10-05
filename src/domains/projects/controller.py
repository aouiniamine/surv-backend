from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from core.dependencies import current_user_id, get_project_service
from core.responses import ApiResponse, success
from domains.organizations.errors import OrganizationAccessDenied, OrganizationNotFound
from domains.projects.dto import CreateProjectRequest, ProjectResponse
from domains.projects.errors import (
    InvalidProjectName,
    OrganizationUnavailable,
    ProjectAccessDenied,
    ProjectNotFound,
)
from domains.projects.service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=ApiResponse[list[ProjectResponse]])
async def list_projects(
    organization_id: UUID | None = None,
    user_id: UUID = Depends(current_user_id),
    service: ProjectService = Depends(get_project_service),
) -> ApiResponse[list[ProjectResponse]]:
    try:
        items = (
            await service.list_for_organization(organization_id, user_id)
            if organization_id is not None
            else await service.list_for_user(user_id)
        )
    except OrganizationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OrganizationAccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    data = [ProjectResponse.model_validate(item) for item in items]
    return success(data, "Projects loaded")


@router.post("", response_model=ApiResponse[ProjectResponse], status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: CreateProjectRequest,
    user_id: UUID = Depends(current_user_id),
    service: ProjectService = Depends(get_project_service),
) -> ApiResponse[ProjectResponse]:
    try:
        project = await service.create(payload.name, payload.organization_id, user_id)
    except InvalidProjectName as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OrganizationUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OrganizationAccessDenied, ProjectAccessDenied) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return success(ProjectResponse.model_validate(project), "Project created", 201)


@router.get("/{project_id}", response_model=ApiResponse[ProjectResponse])
async def get_project(
    project_id: UUID,
    user_id: UUID = Depends(current_user_id),
    service: ProjectService = Depends(get_project_service),
) -> ApiResponse[ProjectResponse]:
    try:
        project = await service.get(project_id, user_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectAccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return success(ProjectResponse.model_validate(project), "Project loaded")
