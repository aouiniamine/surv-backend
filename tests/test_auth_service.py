import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID

import jwt
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from core.dependencies import get_auth_service
from core.responses import register_error_handlers
from domains.auth.controller import router as auth_router
from domains.auth.errors import AccountExists, InvalidOtp, RegistrationNotValidated, Unauthorized
from domains.auth.model import Registration
from domains.auth.service import AuthService
from domains.organizations.model import Organization
from domains.users.model import User

USER_ID = UUID("019535d9-3df7-79fb-b466-fa907fa17f9e")
ORG_ID = UUID("019535d9-3df7-79fb-b466-fa907fa17f9f")
NOW = datetime.now(UTC)
JWT_SECRET = "test-secret-key-with-at-least-32-characters"


class FakeAuthGateway:
    def __init__(self) -> None:
        self.user: User | None = None
        self.registration_args: tuple[str, str, str, str] | None = None

    async def get_user_by_email(self, email: str) -> User | None:
        return self.user if self.user and self.user.email == email else None

    async def get_user_by_id(self, user_id: UUID) -> User | None:
        return self.user if self.user and self.user.id == user_id else None

    async def register(
        self, email: str, first_name: str, last_name: str, organization_name: str
    ) -> Registration:
        self.registration_args = (email, first_name, last_name, organization_name)
        self.user = User(USER_ID, email, first_name, last_name, NOW)
        return Registration(self.user, Organization(ORG_ID, organization_name, NOW))


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, *, ex: int, nx: bool = False) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def getdel(self, key: str) -> str | None:
        return self.values.pop(key, None)

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if self.values.pop(key, None) is not None:
                deleted += 1
        return deleted


class FakeMailer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send_otp(self, email: str, code: str, purpose: str) -> None:
        self.sent.append((email, code, purpose))


class AuthServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repo = FakeAuthGateway()
        self.redis = FakeRedis()
        self.mailer = FakeMailer()
        self.service = AuthService(self.repo, self.redis, self.mailer, JWT_SECRET)

    async def test_registration_requires_otp_then_creates_user_without_session(self) -> None:
        await self.service.start_registration("  ME@Example.COM ")
        email, code, purpose = self.mailer.sent[0]
        self.assertEqual((email, purpose), ("me@example.com", "register"))
        with self.assertRaises(RegistrationNotValidated):
            await self.service.complete_registration(email, "Ada", "Lovelace", "My Org")
        invalid_code = "000000" if code != "000000" else "999999"
        for _ in range(6):
            with self.assertRaises(InvalidOtp):
                await self.service.verify_registration(email, invalid_code)
        await self.service.verify_registration(email, code)
        self.assertIn(f"register:validated:{email}", self.redis.values)
        session = await self.service.complete_registration(email, " Ada ", " Lovelace ", " My Org ")
        self.assertEqual(self.repo.registration_args, (email, "Ada", "Lovelace", "My Org"))
        self.assertEqual(session.registration.user.id.version, 7)
        self.assertEqual(await self.service.current_user_id(session.access_token), USER_ID)
        self.assertFalse(any(key.startswith("session:") for key in self.redis.values))
        with self.assertRaises(RegistrationNotValidated):
            await self.service.complete_registration(email, "Ada", "Lovelace", "My Org")

    async def test_registered_email_cannot_restart_registration(self) -> None:
        self.repo.user = User(USER_ID, "me@example.com", "Ada", "Lovelace", NOW)
        with self.assertRaises(AccountExists):
            await self.service.start_registration("ME@example.com")
        self.assertEqual(self.mailer.sent, [])

    async def test_resending_otp_replaces_previous_code_without_cooldown(self) -> None:
        with patch("domains.auth.service.secrets.randbelow", side_effect=[111111, 222222]):
            await self.service.start_registration("me@example.com")
            await self.service.start_registration("me@example.com")
        self.assertEqual(self.redis.values["register:me@example.com"], "222222")
        with self.assertRaises(InvalidOtp):
            await self.service.verify_registration("me@example.com", "111111")
        await self.service.verify_registration("me@example.com", "222222")

    async def test_login_otp_returns_jwt_without_redis_session(self) -> None:
        self.repo.user = User(USER_ID, "me@example.com", "Ada", "Lovelace", NOW)
        await self.service.start_login("ME@example.com")
        _, code, purpose = self.mailer.sent[0]
        self.assertEqual(purpose, "login")
        authentication = await self.service.verify_login("me@example.com", code)
        token = authentication.access_token
        self.assertEqual(authentication.user, self.repo.user)
        self.assertNotIn("login:me@example.com", self.redis.values)
        with self.assertRaises(InvalidOtp):
            await self.service.verify_login("me@example.com", code)
        self.assertEqual(await self.service.current_user_id(token), USER_ID)
        claims = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        self.assertEqual(claims["sub"], str(USER_ID))
        self.assertEqual(claims["exp"] - claims["iat"], 3 * 24 * 60 * 60)
        self.assertFalse(any(key.startswith("session:") for key in self.redis.values))

    async def test_refresh_returns_new_token_and_current_user(self) -> None:
        self.repo.user = User(USER_ID, "me@example.com", "Ada", "Lovelace", NOW)
        original = self.service._new_token(USER_ID)
        refreshed = await self.service.refresh(original)
        self.assertNotEqual(refreshed.access_token, original)
        self.assertEqual(refreshed.user, self.repo.user)
        self.assertEqual(await self.service.current_user_id(refreshed.access_token), USER_ID)
        claims = jwt.decode(refreshed.access_token, JWT_SECRET, algorithms=["HS256"])
        self.assertEqual(claims["exp"] - claims["iat"], 3 * 24 * 60 * 60)

    async def test_refresh_rejects_invalid_token_and_deleted_user(self) -> None:
        with self.assertRaises(Unauthorized):
            await self.service.refresh(None)
        with self.assertRaises(Unauthorized):
            await self.service.refresh("invalid-token")
        with self.assertRaises(Unauthorized):
            await self.service.refresh(self.service._new_token(USER_ID))

    async def test_login_and_refresh_http_responses_include_user(self) -> None:
        self.repo.user = User(USER_ID, "me@example.com", "Ada", "Lovelace", NOW)
        await self.service.start_login("me@example.com")
        code = self.mailer.sent[0][1]
        app = FastAPI()
        register_error_handlers(app)
        app.include_router(auth_router, prefix="/v1")
        app.dependency_overrides[get_auth_service] = lambda: self.service
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/v1/auth/login/verify", json={"email": "me@example.com", "code": code}
            )
            self.assertEqual(login.status_code, 200)
            self.assertEqual(login.json()["code"], "OK")
            self.assertTrue(login.json()["success"])
            login_body = login.json()["data"]
            self.assertEqual(login_body["token_type"], "bearer")
            self.assertEqual(login_body["user"]["id"], str(USER_ID))
            self.assertEqual(login_body["user"]["email"], "me@example.com")

            refreshed = await client.post(
                "/v1/auth/refresh",
                headers={"Authorization": f"Bearer {login_body['access_token']}"},
            )
            self.assertEqual(refreshed.status_code, 200)
            self.assertEqual(refreshed.json()["data"]["user"], login_body["user"])
            self.assertNotEqual(
                refreshed.json()["data"]["access_token"], login_body["access_token"]
            )
            missing = await client.post("/v1/auth/refresh")
            self.assertEqual(missing.status_code, 401)
            self.assertEqual(missing.json()["code"], "UNAUTHORIZED")
            self.assertFalse(missing.json()["success"])
            invalid = await client.post("/v1/auth/login/verify", json={"email": "bad"})
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(invalid.json()["code"], "UNPROCESSABLE_ENTITY")
            self.assertIsNone(invalid.json()["data"])
            missing_route = await client.get("/v1/does-not-exist")
            self.assertEqual(missing_route.status_code, 404)
            self.assertEqual(missing_route.json()["code"], "NOT_FOUND")

    async def test_registration_http_response_includes_token_and_user(self) -> None:
        await self.service.start_registration("me@example.com")
        code = self.mailer.sent[0][1]
        await self.service.verify_registration("me@example.com", code)
        app = FastAPI()
        register_error_handlers(app)
        app.include_router(auth_router, prefix="/v1")
        app.dependency_overrides[get_auth_service] = lambda: self.service
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/v1/auth/register/complete",
                json={
                    "email": "me@example.com",
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                    "organization_name": "My Org",
                },
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["code"], "CREATED")
        body = response.json()["data"]
        self.assertEqual(body["token_type"], "bearer")
        self.assertEqual(body["user"]["id"], str(USER_ID))
        self.assertEqual(body["organization"]["id"], str(ORG_ID))
        self.assertEqual(await self.service.current_user_id(body["access_token"]), USER_ID)

    async def test_invalid_expired_and_tampered_tokens_are_rejected(self) -> None:
        expired = jwt.encode(
            {
                "sub": str(USER_ID),
                "iat": datetime.now(UTC) - timedelta(minutes=20),
                "exp": datetime.now(UTC) - timedelta(minutes=5),
            },
            JWT_SECRET,
            algorithm="HS256",
        )
        other_secret = jwt.encode(
            {
                "sub": str(USER_ID),
                "iat": datetime.now(UTC),
                "exp": datetime.now(UTC) + timedelta(minutes=15),
            },
            "another-secret-key-with-at-least-32-characters",
            algorithm="HS256",
        )
        for token in (None, "not-a-jwt", expired, other_secret):
            with self.subTest(token=token), self.assertRaises(Unauthorized):
                await self.service.current_user_id(token)

    async def test_login_requires_unexpired_key_and_matching_code(self) -> None:
        self.repo.user = User(USER_ID, "me@example.com", "Ada", "Lovelace", NOW)
        await self.service.start_login("me@example.com")
        _, code, _ = self.mailer.sent[0]
        wrong_code = "000000" if code != "000000" else "999999"
        with self.assertRaises(InvalidOtp):
            await self.service.verify_login("me@example.com", wrong_code)
        self.assertEqual(self.redis.values["login:me@example.com"], code)
        self.redis.values.pop("login:me@example.com")
        with self.assertRaises(InvalidOtp):
            await self.service.verify_login("me@example.com", code)
