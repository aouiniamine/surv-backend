import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID

import jwt

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
        registration = await self.service.complete_registration(
            email, " Ada ", " Lovelace ", " My Org "
        )
        self.assertEqual(self.repo.registration_args, (email, "Ada", "Lovelace", "My Org"))
        self.assertEqual(registration.user.id.version, 7)
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

    async def test_logged_in_user_cannot_start_another_registration(self) -> None:
        token = self.service._new_token(USER_ID)
        with self.assertRaises(AccountExists):
            await self.service.start_registration("other@example.com", token)
        self.assertEqual(self.mailer.sent, [])

    async def test_login_otp_returns_jwt_without_redis_session(self) -> None:
        self.repo.user = User(USER_ID, "me@example.com", "Ada", "Lovelace", NOW)
        await self.service.start_login("ME@example.com")
        _, code, purpose = self.mailer.sent[0]
        self.assertEqual(purpose, "login")
        token = await self.service.verify_login("me@example.com", code)
        self.assertNotIn("login:me@example.com", self.redis.values)
        with self.assertRaises(InvalidOtp):
            await self.service.verify_login("me@example.com", code)
        self.assertEqual(await self.service.current_user_id(token), USER_ID)
        claims = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        self.assertEqual(claims["sub"], str(USER_ID))
        self.assertEqual(claims["exp"] - claims["iat"], 3 * 24 * 60 * 60)
        self.assertFalse(any(key.startswith("session:") for key in self.redis.values))

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
            {"sub": str(USER_ID), "iat": datetime.now(UTC),
             "exp": datetime.now(UTC) + timedelta(minutes=15)},
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
