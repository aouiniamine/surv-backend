import shutil
import unittest
import zipfile
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from core.config import Settings
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
from domains.projects.model import Project, ProjectBackup
from domains.projects.repo import ProjectRepository
from domains.projects.service import ProjectService
from domains.projects.storage import ProjectStorage
from main import create_app

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
        self.backups: list[str] = []

    async def create(self, name: str, organization_id: UUID, user_id: UUID) -> Project | None:
        self.saved_args = (name, organization_id, user_id)
        self.project = Project(
            PROJECT_ID, organization_id, name, "abc1234", "CREATED", datetime.now(UTC)
        )
        return self.project

    async def get(
        self, project_id: UUID, user_id: UUID
    ) -> tuple[Project, OrganizationRole | None] | None:
        return (self.project, self.role) if self.project and self.project.id == project_id else None

    async def get_deployable(self, public_id: str, user_id: UUID) -> Project | None:
        return (
            self.project
            if self.project
            and self.project.public_id == public_id
            and self.role in (OrganizationRole.ADMIN, OrganizationRole.DEVELOPER)
            else None
        )

    async def list_for_organization(self, organization_id: UUID, user_id: UUID) -> list[Project]:
        return [self.project] if self.project is not None else []

    async def list_for_user(self, user_id: UUID) -> list[Project]:
        return [self.project] if self.project is not None and self.role is not None else []

    async def record_deployment(
        self, project_id: UUID, previous_archive_path: str | None
    ) -> list[str]:
        expired: list[str] = []
        if previous_archive_path is not None:
            self.backups.append(previous_archive_path)
            expired = self.backups[:-3]
            self.backups = self.backups[-3:]
        if self.project is not None:
            self.project = replace(self.project, status="DEPLOYED")
        return expired

    async def list_backups(self, project_id: UUID, user_id: UUID) -> list[ProjectBackup]:
        return [
            ProjectBackup(UUID(int=index + 1), path, datetime.now(UTC))
            for index, path in enumerate(reversed(self.backups))
        ]


class ProjectServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.storage = ProjectStorage(Path(temporary.name))

    def project_service(self, store: FakeProjectStore, access: FakeOrganizationAccess):
        return ProjectService(store, access, self.storage)

    async def test_create_normalizes_name_for_developer(self) -> None:
        store = FakeProjectStore(OrganizationRole.DEVELOPER)
        result = await self.project_service(
            store, FakeOrganizationAccess(OrganizationRole.DEVELOPER)
        ).create("  My app  ", ORGANIZATION_ID, USER_ID)
        self.assertEqual(store.saved_args, ("My app", ORGANIZATION_ID, USER_ID))
        self.assertEqual(result.name, "My app")
        self.assertTrue((self.storage.project_dir(result.public_id) / "public").is_dir())

    async def test_repository_retries_public_id_collision(self) -> None:
        @asynccontextmanager
        async def connection():
            yield object()

        engine = SimpleNamespace(begin=connection)
        row = SimpleNamespace(
            id=PROJECT_ID,
            organization_id=ORGANIZATION_ID,
            name="My app",
            public_id="xyz5678",
            status="CREATED",
            created_at=datetime.now(UTC),
        )
        create_project = AsyncMock(side_effect=[None, row])
        with (
            patch("domains.projects.repo.AsyncQuerier") as querier,
            patch("domains.projects.repo.secrets.choice", side_effect="abc1234xyz5678"),
        ):
            querier.return_value.create_project = create_project
            project = await ProjectRepository(engine).create("My app", ORGANIZATION_ID, USER_ID)

        self.assertEqual(project.public_id, "xyz5678")
        self.assertEqual(create_project.await_count, 2)
        self.assertEqual(
            [call.kwargs["public_id"] for call in create_project.await_args_list],
            ["abc1234", "xyz5678"],
        )

    async def test_create_rejects_blank_name_without_writing(self) -> None:
        store = FakeProjectStore()
        with self.assertRaises(InvalidProjectName):
            await self.project_service(store, FakeOrganizationAccess()).create(
                "   ", ORGANIZATION_ID, USER_ID
            )
        self.assertIsNone(store.saved_args)

    async def test_create_denies_qa(self) -> None:
        store = FakeProjectStore(OrganizationRole.QA)
        with self.assertRaises(OrganizationAccessDenied):
            await self.project_service(store, FakeOrganizationAccess(OrganizationRole.QA)).create(
                "My app", ORGANIZATION_ID, USER_ID
            )
        self.assertIsNone(store.saved_args)

    async def test_create_missing_organization(self) -> None:
        with self.assertRaises(OrganizationUnavailable):
            service = self.project_service(FakeProjectStore(), FakeOrganizationAccess(exists=False))
            await service.create("My app", ORGANIZATION_ID, USER_ID)

    async def test_get_missing_and_nonmember(self) -> None:
        store = FakeProjectStore()
        service = self.project_service(store, FakeOrganizationAccess())
        with self.assertRaises(ProjectNotFound):
            await service.get(MISSING_PROJECT_ID, USER_ID)
        await service.create("My app", ORGANIZATION_ID, USER_ID)
        store.role = None
        with self.assertRaises(ProjectAccessDenied):
            await service.get(PROJECT_ID, USER_ID)

    async def test_qa_can_read_organization_projects(self) -> None:
        store = FakeProjectStore(OrganizationRole.QA)
        await self.project_service(store, FakeOrganizationAccess()).create(
            "My app", ORGANIZATION_ID, USER_ID
        )
        service = self.project_service(store, FakeOrganizationAccess(OrganizationRole.QA))
        self.assertEqual((await service.get(PROJECT_ID, USER_ID)).name, "My app")
        self.assertEqual(len(await service.list_for_organization(ORGANIZATION_ID, USER_ID)), 1)

    async def test_project_responses_use_environment_app_url(self) -> None:
        store = FakeProjectStore()
        service = self.project_service(store, FakeOrganizationAccess())
        for environment, domain, expected_url in (
            ("development", None, "http://test/app/abc1234/"),
            ("production", "*.example.com", "https://abc1234.example.com/"),
        ):
            app = create_app(
                Settings(
                    project_uploads_root=str(self.storage.root),
                    project_environment=environment,
                    apps_domain=domain,
                )
            )
            app.dependency_overrides[current_user_id] = lambda: USER_ID
            app.dependency_overrides[get_project_service] = lambda: service
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                created = await client.post(
                    "/v1/projects",
                    json={
                        "name": "My app",
                        "organization_id": str(ORGANIZATION_ID),
                    },
                )
                self.assertEqual(created.status_code, 201)
                self.assertEqual(created.json()["data"]["app_url"], expected_url)
                self.assertEqual(created.json()["data"]["status"], "CREATED")
                listed = await client.get(
                    "/v1/projects",
                    params={
                        "organization_id": str(ORGANIZATION_ID),
                    },
                )
                self.assertEqual(listed.json()["data"][0]["app_url"], expected_url)
                detail = await client.get(f"/v1/projects/{PROJECT_ID}")
                self.assertEqual(detail.json()["data"]["app_url"], expected_url)
                archive_bytes = BytesIO()
                with zipfile.ZipFile(archive_bytes, "w") as archive:
                    archive.writestr("index.html", "<h1>App</h1>")
                uploaded = await client.put(
                    "/v1/projects/abc1234/app",
                    content=archive_bytes.getvalue(),
                    headers={"content-type": "application/zip"},
                )
                self.assertEqual(uploaded.status_code, 200)
                self.assertEqual(uploaded.json()["data"]["url"], expected_url)
                detail = await client.get(f"/v1/projects/{PROJECT_ID}")
                self.assertEqual(detail.json()["data"]["status"], "DEPLOYED")

    async def test_unauthorized_project_requests_return_403_envelope(self) -> None:
        store = FakeProjectStore(OrganizationRole.QA)
        await self.project_service(store, FakeOrganizationAccess()).create(
            "My app", ORGANIZATION_ID, USER_ID
        )
        service = self.project_service(store, FakeOrganizationAccess(OrganizationRole.QA))
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
            response = await client.get(f"/v1/projects/{PROJECT_ID}/backups")
            self.assertEqual(response.status_code, 403)
            service._organizations.role = None
            response = await client.get(
                "/v1/projects", params={"organization_id": str(ORGANIZATION_ID)}
            )
            self.assertEqual(response.status_code, 403)

    async def test_upload_replaces_public_site_and_serves_spa_fallback(self) -> None:
        store = FakeProjectStore()
        service = self.project_service(store, FakeOrganizationAccess())
        project = await service.create("My app", ORGANIZATION_ID, USER_ID)
        app = create_app(Settings(project_uploads_root=str(self.storage.root)))
        app.dependency_overrides[current_user_id] = lambda: USER_ID
        app.dependency_overrides[get_project_service] = lambda: service

        archive_bytes = BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("index.html", "<h1>Deployed</h1>")
            archive.writestr("assets/app.js", "console.log('ready')")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.put(
                f"/v1/projects/{project.public_id}/app",
                content=archive_bytes.getvalue(),
                headers={"content-type": "application/zip"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.json()["data"]["url"], f"http://test/app/{project.public_id}/"
            )
            self.assertEqual(
                (await client.get(f"/app/{project.public_id}/")).text,
                "<h1>Deployed</h1>",
            )
            self.assertEqual(
                (await client.get(f"/app/{project.public_id}/nested/route")).text,
                "<h1>Deployed</h1>",
            )
            self.assertEqual(
                (await client.get(f"/app/{project.public_id}/assets/app.js")).text,
                "console.log('ready')",
            )
            self.assertEqual((await client.get("/assets/app.js")).status_code, 404)
            self.assertEqual(
                (await client.get(f"/app/{project.public_id}/assets/missing.js")).status_code,
                404,
            )
            replacement = BytesIO()
            with zipfile.ZipFile(replacement, "w") as archive:
                archive.writestr("index.html", "<h1>Updated</h1>")
            response = await client.put(
                f"/v1/projects/{project.public_id}/app",
                content=replacement.getvalue(),
                headers={"content-type": "application/zip"},
            )
            self.assertEqual(response.status_code, 200)
            canonical = await client.get(f"/app/{project.public_id}")
            self.assertEqual(canonical.status_code, 307)
            self.assertEqual(canonical.headers["location"], f"http://test/app/{project.public_id}/")
            canonical_page = await client.get(canonical.headers["location"])
            self.assertEqual(canonical_page.text, "<h1>Updated</h1>")
            self.assertFalse(
                (self.storage.project_dir(project.public_id) / "public/assets/app.js").exists()
            )

    async def test_invalid_archive_preserves_existing_site(self) -> None:
        store = FakeProjectStore()
        service = self.project_service(store, FakeOrganizationAccess())
        project = await service.create("My app", ORGANIZATION_ID, USER_ID)
        public = self.storage.project_dir(project.public_id) / "public"
        (public / "index.html").write_text("old", encoding="utf-8")
        app = create_app(Settings(project_uploads_root=str(self.storage.root)))
        app.dependency_overrides[current_user_id] = lambda: USER_ID
        app.dependency_overrides[get_project_service] = lambda: service
        archive_bytes = BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("../outside.html", "bad")
            archive.writestr("index.html", "new")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.put(
                f"/v1/projects/{project.public_id}/app",
                content=archive_bytes.getvalue(),
                headers={"content-type": "application/zip"},
            )
            self.assertEqual(response.status_code, 422)
            self.assertEqual((await client.get(f"/app/{project.public_id}/")).text, "old")
        self.assertFalse((self.storage.root / "outside.html").exists())

    async def test_redeployment_keeps_three_previous_archives(self) -> None:
        store = FakeProjectStore()
        service = self.project_service(store, FakeOrganizationAccess())
        project = await service.create("My app", ORGANIZATION_ID, USER_ID)
        app = create_app(Settings(project_uploads_root=str(self.storage.root)))
        app.dependency_overrides[current_user_id] = lambda: USER_ID
        app.dependency_overrides[get_project_service] = lambda: service
        archives: list[bytes] = []
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for version in range(5):
                output = BytesIO()
                with zipfile.ZipFile(output, "w") as archive:
                    archive.writestr("index.html", f"version {version}")
                archives.append(output.getvalue())
                response = await client.put(
                    f"/v1/projects/{project.public_id}/app",
                    content=archives[-1],
                    headers={"content-type": "application/zip"},
                )
                self.assertEqual(response.status_code, 200)
            self.assertEqual(len(store.backups), 3)
            project_dir = self.storage.project_dir(project.public_id)
            self.assertEqual(
                [(project_dir / path).read_bytes() for path in store.backups], archives[1:4]
            )
            self.assertEqual(len(list((project_dir / "backups").glob("*.zip"))), 3)
            backup_response = await client.get(f"/v1/projects/{project.id}/backups")
            self.assertEqual(backup_response.status_code, 200)
            items = backup_response.json()["data"]
            self.assertEqual(len(items), 3)
            self.assertEqual(
                (await client.get(items[0]["preview_url"])).text,
                "version 3",
            )
            shutil.rmtree(project_dir / "backups" / Path(store.backups[-1]).stem)
            self.assertEqual((await client.get(items[0]["preview_url"])).text, "version 3")
            self.assertEqual(
                (await client.get(f"/app-backups/{project.public_id}/invalid/")).status_code,
                404,
            )
            self.assertEqual((await client.get(f"/app/{project.public_id}/")).text, "version 4")

    async def test_upload_rejects_archive_over_20_mib(self) -> None:
        store = FakeProjectStore()
        service = self.project_service(store, FakeOrganizationAccess())
        project = await service.create("My app", ORGANIZATION_ID, USER_ID)
        app = create_app(Settings(project_uploads_root=str(self.storage.root)))
        app.dependency_overrides[current_user_id] = lambda: USER_ID
        app.dependency_overrides[get_project_service] = lambda: service
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.put(
                f"/v1/projects/{project.public_id}/app",
                content=b"x" * (20 * 1024 * 1024 + 1),
                headers={"content-type": "application/zip"},
            )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(store.project.status, "CREATED")

    async def test_failed_backup_write_restores_previous_deployment(self) -> None:
        store = FakeProjectStore()
        service = self.project_service(store, FakeOrganizationAccess())
        project = await service.create("My app", ORGANIZATION_ID, USER_ID)
        app = create_app(Settings(project_uploads_root=str(self.storage.root)))
        app.dependency_overrides[current_user_id] = lambda: USER_ID
        app.dependency_overrides[get_project_service] = lambda: service

        def archive_for(text: str) -> bytes:
            output = BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("index.html", text)
            return output.getvalue()

        original = archive_for("original")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.put(
                f"/v1/projects/{project.public_id}/app",
                content=original,
                headers={"content-type": "application/zip"},
            )
            self.assertEqual(first.status_code, 200)
            with patch.object(
                store, "record_deployment", side_effect=RuntimeError("database unavailable")
            ):
                with self.assertRaises(RuntimeError):
                    await client.put(
                        f"/v1/projects/{project.public_id}/app",
                        content=archive_for("replacement"),
                        headers={"content-type": "application/zip"},
                    )
            self.assertEqual((await client.get(f"/app/{project.public_id}/")).text, "original")
            self.assertEqual(
                (self.storage.project_dir(project.public_id) / "current.zip").read_bytes(),
                original,
            )
            self.assertEqual(store.backups, [])
            self.assertEqual(
                list((self.storage.project_dir(project.public_id) / "backups").glob("*.zip")),
                [],
            )

    async def test_root_relative_asset_urls_are_served_under_app_path(self) -> None:
        store = FakeProjectStore()
        service = self.project_service(store, FakeOrganizationAccess())
        project = await service.create("My app", ORGANIZATION_ID, USER_ID)
        app = create_app(Settings(project_uploads_root=str(self.storage.root)))
        app.dependency_overrides[current_user_id] = lambda: USER_ID
        app.dependency_overrides[get_project_service] = lambda: service
        archive_bytes = BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr(
                "index.html",
                '<script src="/assets/manifest.js"></script>'
                '<script>window.__reactRouterContext={"basename":"/"}</script>',
            )
            archive.writestr("assets/manifest.js", 'import "/assets/app.js";')
            archive.writestr("assets/app.js", "console.log('ready')")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            uploaded = await client.put(
                f"/v1/projects/{project.public_id}/app",
                content=archive_bytes.getvalue(),
                headers={"content-type": "application/zip"},
            )
            self.assertEqual(uploaded.status_code, 200)
            page = await client.get(f"/app/{project.public_id}/")
            self.assertIn(f"/app/{project.public_id}/assets/manifest.js", page.text)
            self.assertIn(f'"basename":"/app/{project.public_id}/"', page.text)
            manifest = await client.get(f"/app/{project.public_id}/assets/manifest.js")
            self.assertIn(f"/app/{project.public_id}/assets/app.js", manifest.text)
            asset = await client.get(f"/app/{project.public_id}/assets/app.js")
            self.assertEqual(asset.status_code, 200)

    async def test_qa_cannot_upload_app(self) -> None:
        store = FakeProjectStore()
        service = self.project_service(store, FakeOrganizationAccess())
        project = await service.create("My app", ORGANIZATION_ID, USER_ID)
        store.role = OrganizationRole.QA
        app = create_app(Settings(project_uploads_root=str(self.storage.root)))
        app.dependency_overrides[current_user_id] = lambda: USER_ID
        app.dependency_overrides[get_project_service] = lambda: service
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.put(
                f"/v1/projects/{project.public_id}/app",
                content=b"not a zip",
                headers={"content-type": "application/zip"},
            )
        self.assertEqual(response.status_code, 404)
