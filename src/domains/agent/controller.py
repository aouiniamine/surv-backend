import asyncio
import mimetypes
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, RedirectResponse

from core.dependencies import current_user_id, get_agent_service
from core.responses import ApiResponse, success
from domains.agent.dto import (
    AgentChangeDetailResponse,
    AgentChangeResponse,
    AgentDraftResponse,
    AgentEventResponse,
    AgentRunResponse,
    CreateAgentRunRequest,
    PublishAgentDraftRequest,
)
from domains.agent.errors import AgentAccessDenied, AgentConflict, AgentInvalid, AgentNotFound
from domains.agent.preview_auth import (
    allowed_preview_origins,
    issue_preview_session,
    preview_url,
    require_preview_session,
)
from domains.agent.service import AgentService
from domains.projects.controller import project_app_url

router = APIRouter(prefix="/agent", tags=["agent"])
preview_router = APIRouter(prefix="/app", tags=["agent"])
PREVIEW_HEADERS = {
    "Cache-Control": "private, no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": ""
}


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, AgentNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, AgentAccessDenied):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, AgentConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, AgentInvalid):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc


@router.post(
    "/{project_id}/runs",
    response_model=ApiResponse[AgentRunResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_run(
    project_id: UUID,
    payload: CreateAgentRunRequest,
    user_id: UUID = Depends(current_user_id),
    service: AgentService = Depends(get_agent_service),
) -> ApiResponse[AgentRunResponse]:
    try:
        run = await service.create(project_id, user_id, payload.prompt, payload.skill_id)
    except (AgentNotFound, AgentAccessDenied, AgentConflict, AgentInvalid) as exc:
        raise _error(exc) from exc
    return success(AgentRunResponse.from_run(run), "Agent run queued", 202)


@router.get("/{project_id}/runs", response_model=ApiResponse[list[AgentRunResponse]])
async def list_runs(
    project_id: UUID,
    user_id: UUID = Depends(current_user_id),
    service: AgentService = Depends(get_agent_service),
) -> ApiResponse[list[AgentRunResponse]]:
    try:
        runs = await service.runs(project_id, user_id)
    except (AgentNotFound, AgentConflict) as exc:
        raise _error(exc) from exc
    return success([AgentRunResponse.from_run(run) for run in runs], "Agent runs loaded")


@router.get("/{project_id}/runs/{run_id}", response_model=ApiResponse[AgentRunResponse])
async def get_run(
    project_id: UUID,
    run_id: UUID,
    user_id: UUID = Depends(current_user_id),
    service: AgentService = Depends(get_agent_service),
) -> ApiResponse[AgentRunResponse]:
    try:
        run = await service.run(project_id, run_id, user_id)
    except (AgentNotFound, AgentConflict) as exc:
        raise _error(exc) from exc
    return success(AgentRunResponse.from_run(run), "Agent run loaded")


@router.post("/{project_id}/runs/{run_id}/cancel", response_model=ApiResponse[AgentRunResponse])
async def cancel_run(
    project_id: UUID,
    run_id: UUID,
    user_id: UUID = Depends(current_user_id),
    service: AgentService = Depends(get_agent_service),
) -> ApiResponse[AgentRunResponse]:
    try:
        run = await service.cancel(project_id, run_id, user_id)
    except (AgentNotFound, AgentAccessDenied) as exc:
        raise _error(exc) from exc
    return success(AgentRunResponse.from_run(run), "Agent run cancelled")


@router.get(
    "/{project_id}/runs/{run_id}/events",
    response_model=ApiResponse[list[AgentEventResponse]],
)
async def list_events(
    project_id: UUID,
    run_id: UUID,
    after: int = Query(default=0, ge=0),
    user_id: UUID = Depends(current_user_id),
    service: AgentService = Depends(get_agent_service),
) -> ApiResponse[list[AgentEventResponse]]:
    try:
        events = await service.events(project_id, run_id, user_id, after)
    except AgentNotFound as exc:
        raise _error(exc) from exc
    return success([AgentEventResponse.from_event(item) for item in events], "Agent events loaded")


@router.get(
    "/{project_id}/runs/{run_id}/changes",
    response_model=ApiResponse[list[AgentChangeResponse]],
)
async def list_changes(
    project_id: UUID,
    run_id: UUID,
    user_id: UUID = Depends(current_user_id),
    service: AgentService = Depends(get_agent_service),
) -> ApiResponse[list[AgentChangeResponse]]:
    try:
        changes = await service.changes(project_id, run_id, user_id)
    except AgentNotFound as exc:
        raise _error(exc) from exc
    return success(
        [AgentChangeResponse.from_change(item) for item in changes], "Agent changes loaded"
    )


@router.get(
    "/{project_id}/runs/{run_id}/changes/{change_id}",
    response_model=ApiResponse[AgentChangeDetailResponse],
)
async def get_change(
    project_id: UUID,
    run_id: UUID,
    change_id: UUID,
    user_id: UUID = Depends(current_user_id),
    service: AgentService = Depends(get_agent_service),
) -> ApiResponse[AgentChangeDetailResponse]:
    try:
        change = await service.change(project_id, run_id, change_id, user_id)
    except AgentNotFound as exc:
        raise _error(exc) from exc
    return success(AgentChangeDetailResponse.from_change(change), "Agent change loaded")


@router.get("/{project_id}/draft", response_model=ApiResponse[AgentDraftResponse])
async def get_draft(
    project_id: UUID,
    request: Request,
    user_id: UUID = Depends(current_user_id),
    service: AgentService = Depends(get_agent_service),
) -> ApiResponse[AgentDraftResponse]:
    try:
        project = await service.project(project_id, user_id)
        draft = await service.draft(project_id, user_id)
    except (AgentNotFound, AgentConflict) as exc:
        raise _error(exc) from exc
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    return success(
        AgentDraftResponse.from_draft(draft, preview_url(request, project.public_id)),
        "Draft loaded",
    )


@router.post("/{project_id}/preview-session", response_model=ApiResponse[dict[str, str]])
async def create_preview_session(
    project_id: UUID,
    request: Request,
    response: Response,
    user_id: UUID = Depends(current_user_id),
    service: AgentService = Depends(get_agent_service),
) -> ApiResponse[dict[str, str]]:
    try:
        url = await issue_preview_session(project_id, user_id, request, response, service)
    except (AgentNotFound, AgentConflict) as exc:
        raise _error(exc) from exc
    return success({"preview_url": url}, "Preview ready")


@preview_router.get("/{public_id}/dev", include_in_schema=False)
async def preview_root(public_id: str) -> RedirectResponse:
    return RedirectResponse(f"/app/{public_id}/dev/", status_code=307)


@preview_router.get(
    "/{public_id}/dev/{asset_path:path}",
    include_in_schema=False,
    dependencies=[Depends(require_preview_session)],
)
async def preview_asset(
    public_id: str,
    request: Request,
    asset_path: str = "",
) -> Response:
    storage = request.app.state.project_storage
    asset = await asyncio.to_thread(storage.draft_asset, public_id, asset_path)
    if asset is None:
        raise HTTPException(status_code=404, detail="Draft asset not found")
    rewritten = await asyncio.to_thread(
        storage.rewritten_draft_asset, public_id, asset
    )
    origins = allowed_preview_origins(request)
    headers = dict(PREVIEW_HEADERS)
    base_url = urlsplit(str(request.base_url))
    asset_origin = f"{base_url.scheme}://{base_url.netloc}"
    headers["Content-Security-Policy"] = headers["Content-Security-Policy"].format(
        asset_origin=asset_origin,
        frame_ancestors=" ".join(origins) if origins else "'none'",
    )
    if rewritten is not None:
        return Response(
            rewritten,
            media_type=mimetypes.guess_type(asset.name)[0],
            headers=headers,
        )
    return FileResponse(asset, headers=headers)


@router.post("/{project_id}/publish", response_model=ApiResponse[dict[str, str]])
async def publish_draft(
    project_id: UUID,
    payload: PublishAgentDraftRequest,
    request: Request,
    user_id: UUID = Depends(current_user_id),
    service: AgentService = Depends(get_agent_service),
) -> ApiResponse[dict[str, str]]:
    try:
        public_id = await service.publish(project_id, user_id, payload.revision)
    except (AgentNotFound, AgentAccessDenied, AgentConflict, AgentInvalid) as exc:
        raise _error(exc) from exc
    return success({"url": project_app_url(request, public_id)}, "Draft published")
