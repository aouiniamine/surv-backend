from __future__ import annotations

import secrets
from hashlib import sha256
from typing import TYPE_CHECKING
from uuid import UUID

from redis.asyncio import Redis

from domains.auth.errors import (
    AccountExists,
    AccountNotFound,
    InvalidOtp,
    InvalidProfileFields,
    RegistrationNotValidated,
    Unauthorized,
)
from domains.auth.model import Registration

if TYPE_CHECKING:
    from core.email import OtpMailer
    from domains.auth.repo import AuthRepository


CONSUME_OTP_SCRIPT = """
local expected = redis.call('GET', KEYS[1])
if not expected or expected ~= ARGV[1] then return 0 end
local remaining = redis.call('TTL', KEYS[1])
redis.call('DEL', KEYS[1])
return remaining
"""


class AuthService:
    OTP_TTL_SECONDS = 900

    def __init__(
        self,
        repo: AuthRepository,
        redis: Redis,
        mailer: OtpMailer,
        session_ttl_seconds: int,
    ) -> None:
        self._repo = repo
        self._redis = redis
        self._mailer = mailer
        self._session_ttl = session_ttl_seconds

    @staticmethod
    def _email(email: str) -> str:
        return email.strip().lower()

    async def start_registration(self, email: str, session_token: str | None = None) -> None:
        email = self._email(email)
        if session_token and await self._get_session_user_id(session_token) is not None:
            raise AccountExists("You already have an account")
        if await self._repo.get_user_by_email(email) is not None:
            raise AccountExists("You already have an account")
        await self._send_code("register", email)

    async def verify_registration(self, email: str, code: str) -> None:
        email = self._email(email)
        ttl = await self._consume_otp("register", email, code)
        if ttl <= 0:
            raise InvalidOtp("Invalid OTP")
        await self._redis.set(f"register:validated:{email}", "1", ex=ttl)

    async def complete_registration(
        self,
        email: str,
        first_name: str,
        last_name: str,
        organization_name: str,
    ) -> Registration:
        email = self._email(email)
        first_name = first_name.strip()
        last_name = last_name.strip()
        organization_name = organization_name.strip()
        if not first_name or not last_name or not organization_name:
            raise InvalidProfileFields("Names and organization name cannot be blank")
        if await self._redis.getdel(f"register:validated:{email}") is None:
            raise RegistrationNotValidated("Registration verification expired or missing")
        return await self._repo.register(email, first_name, last_name, organization_name)

    async def start_login(self, email: str) -> None:
        email = self._email(email)
        if await self._repo.get_user_by_email(email) is None:
            raise AccountNotFound("Account not found")
        await self._send_code("login", email)

    async def verify_login(self, email: str, code: str) -> str:
        email = self._email(email)
        ttl = await self._consume_otp("login", email, code)
        if ttl <= 0:
            raise InvalidOtp("Invalid OTP")
        user = await self._repo.get_user_by_email(email)
        if user is None:
            raise AccountNotFound("Account not found")
        return await self._new_session(user.id)

    async def current_user_id(self, token: str | None) -> UUID:
        if not token:
            raise Unauthorized("Authentication required")
        user_id = await self._get_session_user_id(token)
        if user_id is None:
            raise Unauthorized("Session expired or invalid")
        return user_id

    async def logout(self, token: str | None) -> None:
        if token:
            await self._redis.delete(self._session_key(token))

    async def _send_code(self, purpose: str, email: str) -> None:
        code = f"{secrets.randbelow(1_000_000):06d}"
        otp_key = f"{purpose}:{email}"
        if purpose == "register":
            await self._redis.delete(f"register:validated:{email}")
        await self._redis.set(otp_key, code, ex=self.OTP_TTL_SECONDS)
        try:
            await self._mailer.send_otp(email, code, purpose)
        except Exception:
            await self._redis.delete(otp_key)
            raise

    async def _consume_otp(self, purpose: str, email: str, code: str) -> int:
        return int(
            await self._redis.eval(
                CONSUME_OTP_SCRIPT,
                1,
                f"{purpose}:{email}",
                code,
            )
        )

    @staticmethod
    def _session_key(token: str) -> str:
        return f"session:{sha256(token.encode()).hexdigest()}"

    async def _get_session_user_id(self, token: str) -> UUID | None:
        value = await self._redis.get(self._session_key(token))
        return UUID(value) if value else None

    async def _new_session(self, user_id: UUID) -> str:
        token = secrets.token_urlsafe(32)
        await self._redis.set(self._session_key(token), str(user_id), ex=self._session_ttl)
        return token
