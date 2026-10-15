"""Validated environment configuration. Never render credentials in diagnostics."""

from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RINGLENS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
        frozen=True,
    )

    falkordb_host: str = Field(default="127.0.0.1", min_length=1, max_length=253)
    falkordb_port: int = Field(default=6379, ge=1, le=65535)
    falkordb_password: SecretStr | None = Field(default=None, repr=False)
    graph_name: str = Field(default="ringlens_demo", pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @field_validator("falkordb_host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        if any(c.isspace() for c in value) or any(c in value for c in "/@?#"):
            raise ValueError(
                "use a hostname or IP address without a URL, credentials, or whitespace"
            )
        return value

    @field_validator("falkordb_password", mode="before")
    @classmethod
    def empty_password(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value
