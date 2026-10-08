import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import Settings
from core.db import engine_lifespan
from core.email import OtpMailer
from core.redis import redis_lifespan
from core.responses import ApiResponse, register_error_handlers, success
from domains.agent.controller import preview_router as agent_preview_router
from domains.agent.controller import router as agent_router
from domains.agent.providers.ollama import OllamaProvider
from domains.agent.repo import AgentRepository
from domains.agent.service import AgentService
from domains.agent.worker import AgentWorker
from domains.agent.workspace import AgentWorkspace
from domains.auth.controller import router as auth_router
from domains.auth.repo import AuthRepository
from domains.auth.service import AuthService
from domains.organizations.controller import router as organizations_router
from domains.organizations.repo import OrganizationRepository
from domains.organizations.service import OrganizationService
from domains.projects.controller import app_router, backup_app_router
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
                project_repo = ProjectRepository(engine)
                project_service = ProjectService(
                    project_repo, organization_service, project_storage
                )
                app.state.project_service = project_service
                agent_repo = AgentRepository(engine)
                agent_workspace = AgentWorkspace(project_storage)
                agent_provider = OllamaProvider(settings.ollama_base_url, settings.ollama_model)
                app.state.agent_service = AgentService(
                    agent_repo, agent_workspace, project_service, settings.ollama_model
                )
                app.state.user_service = UserService(user_repo)
                app.state.organization_service = organization_service
                app.state.auth_service = AuthService(
                    AuthRepository(engine, user_repo, organization_repo),
                    redis,
                    OtpMailer(settings),
                    settings.jwt_secret_key,
                )
                worker_task = (
                    asyncio.create_task(
                        AgentWorker(agent_repo, agent_workspace, agent_provider, project_repo)
                        .run_forever()
                    )
                    if settings.agent_worker_enabled
                    else None
                )
                try:
                    yield
                finally:
                    if worker_task is not None:
                        worker_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await worker_task

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.settings = settings
    app.state.project_storage = project_storage
    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["*"],
            allow_headers=["*"],
            allow_credentials="*" not in origins,
        )
    register_error_handlers(app)
    app.include_router(auth_router, prefix="/v1")
    app.include_router(users_router, prefix="/v1")
    app.include_router(organizations_router, prefix="/v1")
    app.include_router(projects_router, prefix="/v1")
    app.include_router(agent_router, prefix="/v1")
    app.include_router(agent_preview_router)
    app.include_router(app_router)
    app.include_router(backup_app_router)

    @app.get("/health", tags=["health"])
    async def health() -> ApiResponse[dict[str, str]]:
        return success({"status": "ok"}, "Service healthy")

    return app


app = create_app()
