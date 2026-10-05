import asyncio
import mimetypes
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, Response

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
from domains.projects.model import Project
from domains.projects.service import ProjectService
from domains.projects.storage import PUBLIC_ID_PATTERN, AppArchiveTooLarge, InvalidAppArchive

router = APIRouter(prefix="/projects", tags=["projects"])
app_router = APIRouter(prefix="/app", tags=["apps"])


def project_app_url(request: Request, public_id: str) -> str:
    settings = request.app.state.settings
    if settings.project_environment == "production":
        return f"https://{public_id}.{settings.apps_domain}/"
    return f"{str(request.base_url).rstrip('/')}/app/{public_id}/"


def project_response(project: Project, request: Request) -> ProjectResponse:
    return ProjectResponse.from_project(project, project_app_url(request, project.public_id))


@router.get("", response_model=ApiResponse[list[ProjectResponse]])
async def list_projects(
    request: Request,
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
    data = [project_response(item, request) for item in items]
    return success(data, "Projects loaded")


@router.post("", response_model=ApiResponse[ProjectResponse], status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: CreateProjectRequest,
    request: Request,
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
    return success(project_response(project, request), "Project created", 201)


@router.put("/{project_public_id}/app", response_model=ApiResponse[dict[str, str]])
async def upload_project_app(
    project_public_id: str,
    request: Request,
    user_id: UUID = Depends(current_user_id),
    service: ProjectService = Depends(get_project_service),
) -> ApiResponse[dict[str, str]]:
    if not PUBLIC_ID_PATTERN.fullmatch(project_public_id):
        raise HTTPException(status_code=404, detail="Project not found")
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/zip":
        raise HTTPException(status_code=415, detail="Expected an application/zip request body")
    try:
        await service.require_deployable(project_public_id, user_id)
        await service.storage.deploy(project_public_id, request)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AppArchiveTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except InvalidAppArchive as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return success({"url": project_app_url(request, project_public_id)}, "App uploaded")


@router.get("/{project_id}", response_model=ApiResponse[ProjectResponse])
async def get_project(
    project_id: UUID,
    request: Request,
    user_id: UUID = Depends(current_user_id),
    service: ProjectService = Depends(get_project_service),
) -> ApiResponse[ProjectResponse]:
    try:
        project = await service.get(project_id, user_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectAccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return success(project_response(project, request), "Project loaded")


@app_router.get("/{project_public_id}/{asset_path:path}", include_in_schema=False)
async def serve_project_app(
    project_public_id: str, request: Request, asset_path: str = ""
) -> Response:
    try:
        asset = request.app.state.project_storage.asset(project_public_id, asset_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="App not found") from exc
    if asset is None:
        raise HTTPException(status_code=404, detail="App not found")
    rewritten = await asyncio.to_thread(
        request.app.state.project_storage.rewritten_asset, project_public_id, asset
    )
    if rewritten is not None:
        return Response(rewritten, media_type=mimetypes.guess_type(asset.name)[0])
    return FileResponse(asset)
