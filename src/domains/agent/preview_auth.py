"""Callable preview-session issuance and per-asset authentication."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, Response

from core.dependencies import get_agent_service
from domains.agent.errors import AgentConflict, AgentNotFound
from domains.agent.service import AgentService

PREVIEW_COOKIE = "surv_agent_preview"
PREVIEW_AUDIENCE = "surv-agent-preview"
PREVIEW_LIFETIME_SECONDS = 600


def preview_url(request: Request, public_id: str) -> str:
    return f"{str(request.base_url).rstrip('/')}/app/{public_id}/dev/"


def allowed_preview_origins(request: Request) -> list[str]:
    return [
        origin.strip()
        for origin in request.app.state.settings.cors_origins.split(",")
        if origin.strip() and origin.strip() != "*"
    ]


async def issue_preview_session(
    project_id: UUID,
    user_id: UUID,
    request: Request,
    response: Response,
    service: AgentService,
) -> str:
    project = await service.project(project_id, user_id)
    root = await asyncio.to_thread(
        request.app.state.project_storage.dev_asset, project.public_id, ""
    )
    if root is None:
        raise AgentNotFound("Dev app not found")
    if not allowed_preview_origins(request):
        raise HTTPException(status_code=503, detail="Private preview origin is not configured")

    settings = request.app.state.settings
    token = jwt.encode(
        {
            "sub": str(user_id),
            "project_id": str(project_id),
            "public_id": project.public_id,
            "aud": PREVIEW_AUDIENCE,
            "exp": datetime.now(UTC) + timedelta(seconds=PREVIEW_LIFETIME_SECONDS),
        },
        settings.jwt_secret_key,
        algorithm="HS256",
    )
    response.set_cookie(
        PREVIEW_COOKIE,
        token,
        max_age=PREVIEW_LIFETIME_SECONDS,
        path=f"/app/{project.public_id}/dev",
        httponly=True,
        secure=settings.project_environment == "production",
        samesite="lax",
    )
    return preview_url(request, project.public_id)


async def require_preview_session(
    public_id: str,
    request: Request,
    service: AgentService = Depends(get_agent_service),
) -> None:
    """FastAPI dependency: authenticate every requested dev asset."""
    token = request.cookies.get(PREVIEW_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Preview session required")
    try:
        claims = jwt.decode(
            token,
            request.app.state.settings.jwt_secret_key,
            algorithms=["HS256"],
            audience=PREVIEW_AUDIENCE,
        )
        if claims.get("public_id") != public_id:
            raise ValueError("Wrong project")
        project_id = UUID(claims["project_id"])
        user_id = UUID(claims["sub"])
        project = await service.project(project_id, user_id)
        if project.public_id != public_id:
            raise ValueError("Wrong project")
        root = await asyncio.to_thread(
            request.app.state.project_storage.dev_asset, public_id, ""
        )
        if root is None:
            raise ValueError("No dev app")
    except (jwt.PyJWTError, KeyError, TypeError, ValueError, AgentNotFound, AgentConflict) as exc:
        raise HTTPException(status_code=401, detail="Invalid preview session") from exc
