from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import jwt
from redis.asyncio import Redis

from domains.auth.errors import (
    AccountExists,
    AccountNotFound,
    InvalidOtp,
    InvalidProfileFields,
    RegistrationNotValidated,
    Unauthorized,
)
from domains.auth.model import Authentication, RegistrationSession

if TYPE_CHECKING:
    from core.email import OtpMailer
    from domains.auth.repo import AuthRepository


class AuthService:
    OTP_TTL_SECONDS = 900
    JWT_TTL_SECONDS = 3 * 24 * 60 * 60

    def __init__(
        self,
        repo: AuthRepository,
        redis: Redis,
        mailer: OtpMailer,
        jwt_secret_key: str,
    ) -> None:
        self._repo = repo
        self._redis = redis
        self._mailer = mailer
        self._jwt_secret_key = jwt_secret_key

    @staticmethod
    def _email(email: str) -> str:
        return email.strip().lower()

    async def start_registration(self, email: str) -> None:
        email = self._email(email)
        if await self._repo.get_user_by_email(email) is not None:
            raise AccountExists("You already have an account")
        await self._send_code("register", email)

    async def verify_registration(self, email: str, code: str) -> None:
        email = self._email(email)
        otp_key = await self._validate_otp("register", email, code)
        if await self._redis.delete(otp_key) != 1:
            raise InvalidOtp("Invalid OTP")
        await self._redis.set(f"register:validated:{email}", "1", ex=self.OTP_TTL_SECONDS)

    async def complete_registration(
        self,
        email: str,
        first_name: str,
        last_name: str,
        organization_name: str,
    ) -> RegistrationSession:
        email = self._email(email)
        first_name = first_name.strip()
        last_name = last_name.strip()
        organization_name = organization_name.strip()
        if not first_name or not last_name or not organization_name:
            raise InvalidProfileFields("Names and organization name cannot be blank")
        if await self._redis.getdel(f"register:validated:{email}") is None:
            raise RegistrationNotValidated("Registration verification expired or missing")
        registration = await self._repo.register(email, first_name, last_name, organization_name)
        return RegistrationSession(
            access_token=self._new_token(registration.user.id),
            registration=registration,
        )

    async def start_login(self, email: str) -> None:
        email = self._email(email)
        if await self._repo.get_user_by_email(email) is None:
            raise AccountNotFound("Account not found")
        await self._send_code("login", email)

    async def verify_login(self, email: str, code: str) -> Authentication:
        email = self._email(email)
        otp_key = await self._validate_otp("login", email, code)
        if await self._redis.delete(otp_key) != 1:
            raise InvalidOtp("Invalid OTP")
        user = await self._repo.get_user_by_email(email)
        if user is None:
            raise AccountNotFound("Account not found")
        return Authentication(access_token=self._new_token(user.id), user=user)

    async def refresh(self, token: str | None) -> Authentication:
        user_id = await self.current_user_id(token)
        user = await self._repo.get_user_by_id(user_id)
        if user is None:
            raise Unauthorized("Account no longer exists")
        return Authentication(access_token=self._new_token(user.id), user=user)

    async def current_user_id(self, token: str | None) -> UUID:
        if not token:
            raise Unauthorized("Authentication required")
        user_id = self._token_user_id(token)
        if user_id is None:
            raise Unauthorized("Token expired or invalid")
        return user_id

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

    async def _validate_otp(self, purpose: str, email: str, code: str) -> str:
        otp_key = f"{purpose}:{email}"
        expected = await self._redis.get(otp_key)
        if expected is None or not secrets.compare_digest(expected, code):
            raise InvalidOtp("Invalid OTP")
        return otp_key

    def _token_user_id(self, token: str) -> UUID | None:
        try:
            claims = jwt.decode(
                token,
                self._jwt_secret_key,
                algorithms=["HS256"],
                options={"require": ["sub", "iat", "exp"]},
            )
            return UUID(claims["sub"])
        except (jwt.InvalidTokenError, TypeError, ValueError):
            return None

    def _new_token(self, user_id: UUID) -> str:
        issued_at = datetime.now(UTC)
        return jwt.encode(
            {
                "sub": str(user_id),
                "jti": str(uuid4()),
                "iat": issued_at,
                "exp": issued_at + timedelta(seconds=self.JWT_TTL_SECONDS),
            },
            self._jwt_secret_key,
            algorithm="HS256",
        )
