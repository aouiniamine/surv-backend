from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from core.dependencies import current_user_id, get_project_service
from domains.projects.dto import CreateProjectRequest, ProjectResponse
from domains.projects.errors import InvalidProjectName, OrganizationUnavailable, ProjectNotFound
from domains.projects.service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: CreateProjectRequest,
    user_id: UUID = Depends(current_user_id),
    service: ProjectService = Depends(get_project_service),
) -> ProjectResponse:
    try:
        project = await service.create(payload.name, payload.organization_id, user_id)
    except InvalidProjectName as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OrganizationUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProjectResponse.model_validate(project)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    user_id: UUID = Depends(current_user_id),
    service: ProjectService = Depends(get_project_service),
) -> ProjectResponse:
    try:
        project = await service.get(project_id, user_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProjectResponse.model_validate(project)
