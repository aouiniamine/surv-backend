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
    smtp_start_tls: bool = False
    session_ttl_seconds: int = Field(default=604800, gt=0)

    @model_validator(mode="after")
    def validate_smtp_tls(self) -> "Settings":
        if self.smtp_use_tls and self.smtp_start_tls:
            raise ValueError("Use either SMTP_USE_TLS or SMTP_START_TLS, not both")
        return self

    @property
    def async_database_url(self) -> str:
        url = make_url(self.database_url)
        if url.get_backend_name() != "postgresql":
            raise ValueError("DATABASE_URL must use PostgreSQL")
        return url.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
