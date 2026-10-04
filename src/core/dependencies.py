from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from domains.auth.errors import Unauthorized
from domains.auth.service import AuthService
from domains.organizations.service import OrganizationService
from domains.projects.service import ProjectService
from domains.users.service import UserService

bearer_scheme = HTTPBearer(auto_error=False)


def get_project_service(request: Request) -> ProjectService:
    return request.app.state.project_service


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def get_user_service(request: Request) -> UserService:
    return request.app.state.user_service


def get_organization_service(request: Request) -> OrganizationService:
    return request.app.state.organization_service


async def current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    service: AuthService = Depends(get_auth_service),
) -> UUID:
    try:
        return await service.current_user_id(credentials.credentials if credentials else None)
    except Unauthorized as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
