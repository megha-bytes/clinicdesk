"""Typed settings. Non-secret config comes from env vars; secrets via SecretsProvider."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.secrets import SecretNotFound, get_secrets_provider

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env.local"


def _testing() -> bool:
    # Tests must never read the developer's real keys from .env.local.
    return os.environ.get("APP_ENV") == "test"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None if _testing() else (ENV_FILE,),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "dev"                      # dev | prod | test
    database_url: str = "sqlite:///./clinicdesk.db"
    token_factory_base_url: str = "https://api.tokenfactory.nebius.com/v1/"
    cors_origins: str = "http://localhost:5173"
    daily_spend_cap_usd: float = 2.0
    public_base_url: str = "http://localhost:8000"   # used to build mock payment links
    payment_provider: str = "mock"                    # mock | razorpay (test mode only)

    otel_enabled: bool = True
    otel_service_name: str = "clinicdesk"
    otel_exporter_otlp_endpoint: str | None = None   # e.g. http://localhost:4317

    @field_validator("database_url", "token_factory_base_url", "cors_origins", "public_base_url", "payment_provider", mode="before")
    @classmethod
    def _blank_means_default(cls, v, info):
        # A blank line copied from .env.example (e.g. "DATABASE_URL=") falls back to the default.
        if v is None or (isinstance(v, str) and not v.strip()):
            return cls.model_fields[info.field_name].default
        return v

    def secret(self, name: str) -> SecretStr | None:
        """Fetch a secret through the configured provider. Returns None if absent."""
        try:
            return SecretStr(get_secrets_provider().get(name))
        except SecretNotFound:
            return None

    @property
    def token_factory_api_key(self) -> SecretStr | None:
        return self.secret("TOKEN_FACTORY_API_KEY")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # Load .env.local into the process env too, so EnvProvider can see secrets in dev.
    if not _testing() and ENV_FILE.exists():
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE, override=False)
    return s
