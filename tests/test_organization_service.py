import unittest
from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from core.dependencies import current_user_id, get_organization_service
from core.responses import register_error_handlers
from domains.organizations.controller import router as organization_router
from domains.organizations.errors import (
    InvalidOrganizationName,
    OrganizationAccessDenied,
    OrganizationNotFound,
)
from domains.organizations.model import (
    ROLE_PRIORITY,
    Organization,
    OrganizationRole,
    UserOrganization,
)
from domains.organizations.service import OrganizationService

USER_ID = UUID("019535d9-3df7-79fb-b466-fa907fa17fa1")
ORGANIZATION_ID = UUID("019535d9-3df7-79fb-b466-fa907fa17fa0")


class FakeOrganizationRepository:
    def __init__(self) -> None:
        self.created: tuple[str, UUID] | None = None
        self.role: OrganizationRole | None = OrganizationRole.ADMIN
        self.exists = True

    async def create_for_user(self, name: str, user_id: UUID) -> UserOrganization:
        self.created = (name, user_id)
        return UserOrganization(ORGANIZATION_ID, name, OrganizationRole.ADMIN, datetime.now(UTC))

    async def get_access(
        self, organization_id: UUID, user_id: UUID
    ) -> tuple[Organization, OrganizationRole | None] | None:
        if not self.exists:
            return None
        return Organization(ORGANIZATION_ID, "Design Team", datetime.now(UTC)), self.role


class OrganizationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_role_priorities_and_access(self) -> None:
        self.assertEqual(
            ROLE_PRIORITY,
            {
                OrganizationRole.ADMIN: 999,
                OrganizationRole.DEVELOPER: 777,
                OrganizationRole.QA: 333,
            },
        )
        repo = FakeOrganizationRepository()
        service = OrganizationService(repo)
        for role in OrganizationRole:
            repo.role = role
            self.assertEqual(
                (await service.require_access(ORGANIZATION_ID, USER_ID, OrganizationRole.QA)).role,
                role,
            )
        repo.role = OrganizationRole.QA
        with self.assertRaises(OrganizationAccessDenied):
            await service.require_access(ORGANIZATION_ID, USER_ID, OrganizationRole.DEVELOPER)
        repo.role = None
        with self.assertRaises(OrganizationAccessDenied):
            await service.require_access(ORGANIZATION_ID, USER_ID, OrganizationRole.QA)
        repo.exists = False
        with self.assertRaises(OrganizationNotFound):
            await service.require_access(ORGANIZATION_ID, USER_ID, OrganizationRole.QA)

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
            repo.role = None
            forbidden = await client.get(f"/v1/organizations/{ORGANIZATION_ID}")
            self.assertEqual(forbidden.status_code, 403)
            self.assertEqual(forbidden.json()["code"], "FORBIDDEN")
