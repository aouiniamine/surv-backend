import unittest
from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from core.dependencies import current_user_id, get_organization_service
from core.responses import register_error_handlers
from domains.organizations.controller import router as organization_router
from domains.organizations.errors import InvalidOrganizationName
from domains.organizations.model import OrganizationRole, UserOrganization
from domains.organizations.service import OrganizationService

USER_ID = UUID("019535d9-3df7-79fb-b466-fa907fa17fa1")
ORGANIZATION_ID = UUID("019535d9-3df7-79fb-b466-fa907fa17fa0")


class FakeOrganizationRepository:
    def __init__(self) -> None:
        self.created: tuple[str, UUID] | None = None

    async def create_for_user(self, name: str, user_id: UUID) -> UserOrganization:
        self.created = (name, user_id)
        return UserOrganization(ORGANIZATION_ID, name, OrganizationRole.ADMIN, datetime.now(UTC))


class OrganizationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_normalizes_name_and_assigns_admin(self) -> None:
        repo = FakeOrganizationRepository()
        result = await OrganizationService(repo).create_for_user("  Design Team  ", USER_ID)
        self.assertEqual(repo.created, ("Design Team", USER_ID))
        self.assertEqual(result.role, OrganizationRole.ADMIN)

    async def test_create_rejects_blank_and_oversized_names(self) -> None:
        repo = FakeOrganizationRepository()
        for name in ("   ", "x" * 151):
            with self.subTest(name=name), self.assertRaises(InvalidOrganizationName):
                await OrganizationService(repo).create_for_user(name, USER_ID)
        self.assertIsNone(repo.created)

    async def test_create_route_returns_enveloped_organization(self) -> None:
        repo = FakeOrganizationRepository()
        app = FastAPI()
        register_error_handlers(app)
        app.include_router(organization_router, prefix="/v1")
        app.dependency_overrides[current_user_id] = lambda: USER_ID
        app.dependency_overrides[get_organization_service] = lambda: OrganizationService(repo)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/v1/organizations", json={"name": " Design Team "})
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json()["code"], "CREATED")
            self.assertEqual(response.json()["data"]["name"], "Design Team")
            self.assertEqual(response.json()["data"]["role"], "ADMIN")
            invalid = await client.post("/v1/organizations", json={"name": "   "})
            self.assertEqual(invalid.status_code, 422)
            self.assertFalse(invalid.json()["success"])
