import unittest
from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from core.dependencies import current_user_id, get_project_service
from core.responses import register_error_handlers
from domains.organizations.errors import OrganizationAccessDenied, OrganizationNotFound
from domains.organizations.model import ROLE_PRIORITY, OrganizationRole
from domains.projects.controller import router as project_router
from domains.projects.errors import (
    InvalidProjectName,
    OrganizationUnavailable,
    ProjectAccessDenied,
    ProjectNotFound,
)
from domains.projects.model import Project
from domains.projects.service import ProjectService

PROJECT_ID = UUID("019535d9-3df7-79fb-b466-fa907fa17f9e")
MISSING_PROJECT_ID = UUID("019535d9-3df7-79fb-b466-fa907fa17f9f")
ORGANIZATION_ID = UUID("019535d9-3df7-79fb-b466-fa907fa17fa0")
USER_ID = UUID("019535d9-3df7-79fb-b466-fa907fa17fa1")


class FakeOrganizationAccess:
    def __init__(
        self, role: OrganizationRole | None = OrganizationRole.ADMIN, exists: bool = True
    ) -> None:
        self.role = role
        self.exists = exists

    async def require_access(
        self, organization_id: UUID, user_id: UUID, minimum_role: OrganizationRole
    ) -> None:
        if not self.exists:
            raise OrganizationNotFound("Organization not found")
        if self.role is None or ROLE_PRIORITY[self.role] < ROLE_PRIORITY[minimum_role]:
            raise OrganizationAccessDenied("Insufficient organization access")


class FakeProjectStore:
    def __init__(self, role: OrganizationRole | None = OrganizationRole.ADMIN) -> None:
        self.saved_args: tuple[str, UUID, UUID] | None = None
        self.project: Project | None = None
        self.role = role

    async def create(self, name: str, organization_id: UUID, user_id: UUID) -> Project | None:
        self.saved_args = (name, organization_id, user_id)
        self.project = Project(PROJECT_ID, organization_id, name, datetime.now(UTC))
        return self.project

    async def get(
        self, project_id: UUID, user_id: UUID
    ) -> tuple[Project, OrganizationRole | None] | None:
        return (self.project, self.role) if self.project and self.project.id == project_id else None

    async def list_for_organization(self, organization_id: UUID, user_id: UUID) -> list[Project]:
        return [self.project] if self.project is not None else []

    async def list_for_user(self, user_id: UUID) -> list[Project]:
        return [self.project] if self.project is not None and self.role is not None else []


class ProjectServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_normalizes_name_for_developer(self) -> None:
        store = FakeProjectStore(OrganizationRole.DEVELOPER)
        result = await ProjectService(
            store, FakeOrganizationAccess(OrganizationRole.DEVELOPER)
        ).create("  My app  ", ORGANIZATION_ID, USER_ID)
        self.assertEqual(store.saved_args, ("My app", ORGANIZATION_ID, USER_ID))
        self.assertEqual(result.name, "My app")

    async def test_create_rejects_blank_name_without_writing(self) -> None:
        store = FakeProjectStore()
        with self.assertRaises(InvalidProjectName):
            await ProjectService(store, FakeOrganizationAccess()).create(
                "   ", ORGANIZATION_ID, USER_ID
            )
        self.assertIsNone(store.saved_args)

    async def test_create_denies_qa(self) -> None:
        store = FakeProjectStore(OrganizationRole.QA)
        with self.assertRaises(OrganizationAccessDenied):
            await ProjectService(store, FakeOrganizationAccess(OrganizationRole.QA)).create(
                "My app", ORGANIZATION_ID, USER_ID
            )
        self.assertIsNone(store.saved_args)

    async def test_create_missing_organization(self) -> None:
        with self.assertRaises(OrganizationUnavailable):
            await ProjectService(FakeProjectStore(), FakeOrganizationAccess(exists=False)).create(
                "My app", ORGANIZATION_ID, USER_ID
            )

    async def test_get_missing_and_nonmember(self) -> None:
        store = FakeProjectStore()
        service = ProjectService(store, FakeOrganizationAccess())
        with self.assertRaises(ProjectNotFound):
            await service.get(MISSING_PROJECT_ID, USER_ID)
        await service.create("My app", ORGANIZATION_ID, USER_ID)
        store.role = None
        with self.assertRaises(ProjectAccessDenied):
            await service.get(PROJECT_ID, USER_ID)

    async def test_qa_can_read_organization_projects(self) -> None:
        store = FakeProjectStore(OrganizationRole.QA)
        await ProjectService(store, FakeOrganizationAccess()).create(
            "My app", ORGANIZATION_ID, USER_ID
        )
        service = ProjectService(store, FakeOrganizationAccess(OrganizationRole.QA))
        self.assertEqual((await service.get(PROJECT_ID, USER_ID)).name, "My app")
        self.assertEqual(len(await service.list_for_organization(ORGANIZATION_ID, USER_ID)), 1)

    async def test_unauthorized_project_requests_return_403_envelope(self) -> None:
        store = FakeProjectStore(OrganizationRole.QA)
        await ProjectService(store, FakeOrganizationAccess()).create(
            "My app", ORGANIZATION_ID, USER_ID
        )
        service = ProjectService(store, FakeOrganizationAccess(OrganizationRole.QA))
        app = FastAPI()
        register_error_handlers(app)
        app.include_router(project_router, prefix="/v1")
        app.dependency_overrides[current_user_id] = lambda: USER_ID
        app.dependency_overrides[get_project_service] = lambda: service
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/v1/projects", json={"name": "Blocked", "organization_id": str(ORGANIZATION_ID)}
            )
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["code"], "FORBIDDEN")
            store.role = None
            response = await client.get(f"/v1/projects/{PROJECT_ID}")
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["code"], "FORBIDDEN")
            service._organizations.role = None
            response = await client.get(
                "/v1/projects", params={"organization_id": str(ORGANIZATION_ID)}
            )
            self.assertEqual(response.status_code, 403)
