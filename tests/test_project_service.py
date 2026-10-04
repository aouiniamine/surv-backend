import unittest
from datetime import UTC, datetime
from uuid import UUID

from domains.projects.errors import InvalidProjectName, OrganizationUnavailable, ProjectNotFound
from domains.projects.model import Project
from domains.projects.service import ProjectService

PROJECT_ID = UUID("019535d9-3df7-79fb-b466-fa907fa17f9e")
MISSING_PROJECT_ID = UUID("019535d9-3df7-79fb-b466-fa907fa17f9f")
ORGANIZATION_ID = UUID("019535d9-3df7-79fb-b466-fa907fa17fa0")
USER_ID = UUID("019535d9-3df7-79fb-b466-fa907fa17fa1")
OTHER_USER_ID = UUID("019535d9-3df7-79fb-b466-fa907fa17fa2")


class FakeProjectStore:
    def __init__(self) -> None:
        self.saved_args: tuple[str, UUID, UUID] | None = None
        self.project: Project | None = None

    async def create(self, name: str, organization_id: UUID, user_id: UUID) -> Project | None:
        self.saved_args = (name, organization_id, user_id)
        if organization_id != ORGANIZATION_ID or user_id != USER_ID:
            return None
        self.project = Project(
            id=PROJECT_ID,
            organization_id=organization_id,
            name=name,
            created_at=datetime.now(UTC),
        )
        return self.project

    async def get(self, project_id: UUID, user_id: UUID) -> Project | None:
        if self.project is not None and self.project.id == project_id and user_id == USER_ID:
            return self.project
        return None


class ProjectServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_normalizes_name_before_persisting(self) -> None:
        store = FakeProjectStore()
        result = await ProjectService(store).create("  My app  ", ORGANIZATION_ID, USER_ID)
        self.assertEqual(store.saved_args, ("My app", ORGANIZATION_ID, USER_ID))
        self.assertEqual(result.name, "My app")
        self.assertEqual(result.organization_id, ORGANIZATION_ID)

    async def test_create_rejects_blank_name_without_writing(self) -> None:
        store = FakeProjectStore()
        with self.assertRaises(InvalidProjectName):
            await ProjectService(store).create("   ", ORGANIZATION_ID, USER_ID)
        self.assertIsNone(store.saved_args)

    async def test_create_requires_organization_membership(self) -> None:
        with self.assertRaises(OrganizationUnavailable):
            await ProjectService(FakeProjectStore()).create("My app", ORGANIZATION_ID, OTHER_USER_ID)

    async def test_get_reports_missing_project(self) -> None:
        with self.assertRaises(ProjectNotFound):
            await ProjectService(FakeProjectStore()).get(MISSING_PROJECT_ID, USER_ID)

    async def test_get_hides_project_from_nonmember(self) -> None:
        store = FakeProjectStore()
        await ProjectService(store).create("My app", ORGANIZATION_ID, USER_ID)
        with self.assertRaises(ProjectNotFound):
            await ProjectService(store).get(PROJECT_ID, OTHER_USER_ID)
