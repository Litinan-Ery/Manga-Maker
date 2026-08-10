from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration. Secrets are deliberately excluded."""

    model_config = SettingsConfigDict(
        env_prefix="MANGA_MAKER_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Manga Maker"
    app_version: str = "0.2.0"
    environment: str = "development"
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=0, ge=0, le=65535)
    app_data_dir: Path = Field(
        default_factory=lambda: Path.home() / "Library" / "Application Support" / "Manga Maker"
    )

    @field_validator("bind_host")
    @classmethod
    def validate_loopback_host(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized == "localhost":
            return normalized
        try:
            address = ipaddress.ip_address(normalized)
        except ValueError as exc:
            raise ValueError("bind_host must be localhost or a loopback IP address") from exc
        if not address.is_loopback:
            raise ValueError("Manga Maker may only bind to a loopback address")
        return normalized

    @property
    def database_path(self) -> Path:
        return self.app_data_dir / "manga_maker.sqlite3"

    @property
    def projects_dir(self) -> Path:
        return self.app_data_dir / "projects"

    @property
    def secrets_dir(self) -> Path:
        return self.app_data_dir / "secrets"

    @property
    def vault_path(self) -> Path:
        return self.secrets_dir / "credentials.vault"

    @property
    def frontend_dist_dir(self) -> Path:
        return Path(__file__).resolve().parents[2] / "frontend" / "dist"

    def ensure_directories(self) -> None:
        self.app_data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.projects_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.secrets_dir.mkdir(mode=0o700, parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
