from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import Settings
from core.db import engine_lifespan
from core.email import OtpMailer
from core.redis import redis_lifespan
from core.responses import ApiResponse, register_error_handlers, success
from domains.auth.controller import router as auth_router
from domains.auth.repo import AuthRepository
from domains.auth.service import AuthService
from domains.organizations.controller import router as organizations_router
from domains.organizations.repo import OrganizationRepository
from domains.organizations.service import OrganizationService
from domains.projects.controller import app_router
from domains.projects.controller import router as projects_router
from domains.projects.repo import ProjectRepository
from domains.projects.service import ProjectService
from domains.projects.storage import ProjectStorage
from domains.users.controller import router as users_router
from domains.users.repo import UserRepository
from domains.users.service import UserService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    project_storage = ProjectStorage(Path(settings.project_uploads_root))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with engine_lifespan(settings) as engine:
            async with redis_lifespan(settings) as redis:
                user_repo = UserRepository(engine)
                organization_repo = OrganizationRepository(engine)
                organization_service = OrganizationService(organization_repo)
                app.state.project_service = ProjectService(
                    ProjectRepository(engine), organization_service,
                    project_storage,
                )
                app.state.user_service = UserService(user_repo)
                app.state.organization_service = organization_service
                app.state.auth_service = AuthService(
                    AuthRepository(engine, user_repo, organization_repo),
                    redis,
                    OtpMailer(settings),
                    settings.jwt_secret_key,
                )
                yield

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.settings = settings
    app.state.project_storage = project_storage
    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"]
        )
    register_error_handlers(app)
    app.include_router(auth_router, prefix="/v1")
    app.include_router(users_router, prefix="/v1")
    app.include_router(organizations_router, prefix="/v1")
    app.include_router(projects_router, prefix="/v1")
    app.include_router(app_router)

    @app.get("/health", tags=["health"])
    async def health() -> ApiResponse[dict[str, str]]:
        return success({"status": "ok"}, "Service healthy")

    return app


app = create_app()
