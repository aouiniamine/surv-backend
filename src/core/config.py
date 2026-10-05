import re
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Surv API"
    database_url: str = Field(..., description="PostgreSQL URL shared with dbmate")
    redis_url: str = "redis://localhost:6379/0"
    smtp_host: str = Field(..., min_length=1)
    smtp_port: int = Field(..., ge=1, le=65535)
    smtp_username: str = Field(..., min_length=1)
    smtp_password: str = Field(..., min_length=1)
    smtp_from_email: str = Field(..., min_length=1)
    smtp_use_tls: bool = False
    smtp_start_tls: bool = True
    jwt_secret_key: str = Field(..., min_length=32)
    cors_origins: str = "*"
    project_uploads_root: str = "./uploads/projects"
    project_environment: Literal["dev", "development", "staging", "production"] = "development"
    apps_domain: str | None = None

    @model_validator(mode="after")
    def validate_smtp_tls(self) -> "Settings":
        if self.smtp_use_tls and self.smtp_start_tls:
            raise ValueError("Use either SMTP_USE_TLS or SMTP_START_TLS, not both")
        if self.apps_domain is not None:
            self.apps_domain = self.apps_domain.strip().lower().removeprefix("*.")
            if not re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z0-9-]+", self.apps_domain):
                raise ValueError("APPS_DOMAIN must be a domain such as example.com")
        if self.project_environment == "production" and not self.apps_domain:
            raise ValueError("APPS_DOMAIN is required in production")
        return self

    @property
    def async_database_url(self) -> str:
        url = make_url(self.database_url)
        if url.get_backend_name() != "postgresql":
            raise ValueError("DATABASE_URL must use PostgreSQL")
        return url.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
