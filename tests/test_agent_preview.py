import asyncio
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from core.dependencies import current_user_id, get_agent_service
from domains.agent.controller import preview_router, router
from domains.agent.errors import AgentNotFound
from domains.agent.model import AgentProject
from domains.organizations.model import OrganizationRole
from domains.projects.storage import ProjectStorage


class PreviewService:
    def __init__(self, project):
        self.project_value = project
        self.revoked = False

    async def project(self, project_id, user_id):
        if self.revoked:
            raise AgentNotFound("Project not found")
        return self.project_value


def test_preview_uses_dev_files_without_draft_record_and_requires_membership(tmp_path):
    project_id = uuid4()
    user_id = uuid4()
    project = AgentProject(project_id, "abc1234", "CREATED", OrganizationRole.QA)
    service = PreviewService(project)
    public = tmp_path / "abc1234" / "dev" / "public"
    public.mkdir(parents=True)
    (public / "index.html").write_text(
        '<link rel="stylesheet" href="/style.css"><h1>Draft</h1>', encoding="utf-8"
    )
    (public / "style.css").write_text("h1 { color: green; }", encoding="utf-8")
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        jwt_secret_key="test-secret-key-long-enough-for-jwt-signing",
        project_environment="development",
        cors_origins="http://client.test",
    )
    app.state.project_storage = ProjectStorage(tmp_path)
    app.include_router(router, prefix="/v1")
    app.include_router(preview_router)
    app.dependency_overrides[current_user_id] = lambda: user_id
    app.dependency_overrides[get_agent_service] = lambda: service

    async def scenario():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://api.test"
        ) as client:
            asset = "/app/abc1234/dev/index.html"
            root_redirect = await client.get("/app/abc1234/dev")
            assert root_redirect.status_code == 307
            assert root_redirect.headers["location"] == "/app/abc1234/dev/"
            assert (await client.get(asset)).status_code == 401
            app.state.settings.cors_origins = "*"
            assert (await client.post(f"/v1/agent/{project_id}/preview-session")).status_code == 503
            app.state.settings.cors_origins = "http://client.test"
            session = await client.post(f"/v1/agent/{project_id}/preview-session")
            assert session.status_code == 200
            assert session.json()["data"]["preview_url"] == "http://api.test/app/abc1234/dev/"
            response = await client.get(asset)
            assert response.status_code == 200
            assert (await client.get("/app/other12/dev/index.html")).status_code == 401
            assert "Draft" in response.text
            assert 'href="/app/abc1234/dev/style.css"' in response.text
            assert (await client.get("/app/abc1234/dev/style.css")).status_code == 200
            assert response.headers["cache-control"] == "private, no-store"
            assert "frame-ancestors http://client.test" in response.headers[
                "content-security-policy"
            ]
            assert "sandbox allow-scripts allow-forms allow-same-origin" in response.headers[
                "content-security-policy"
            ]
            assert "https://cdn.jsdelivr.net" in response.headers["content-security-policy"]
            service.revoked = True
            assert (await client.get(asset)).status_code == 401

    asyncio.run(scenario())
