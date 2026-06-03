"""Application settings (env overrides config file paths)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SENSERVE_", extra="ignore")

    api_port: int = 8787
    worker_base_port: int = Field(
        default=8000,
        validation_alias=AliasChoices("worker_base_port", "worker_port"),
    )
    models_path: Path = Field(default=_REPO_ROOT / "config" / "models.toml")
    default_model_id: str = "qwen3.5-0.8b"
    max_upload_bytes: int = 300 * 1024 * 1024
    # When false, chat bodies are forwarded to vLLM unchanged (besides capability checks).
    inline_remote_media: bool = False
    validate_capabilities: bool = True
    worker_ready_timeout_s: float = 600.0
    switch_retry_after_s: int = 30
    sleep_mode: Literal["off", "level1", "level2"] = "level2"
    max_workers: int = 16
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:8788", "http://127.0.0.1:8788"]
    )

    @property
    def worker_port(self) -> int:
        """Backward-compatible alias for worker_base_port."""
        return self.worker_base_port

    @property
    def sleep_enabled(self) -> bool:
        return self.sleep_mode != "off"

    @property
    def sleep_level(self) -> int:
        return 2 if self.sleep_mode == "level2" else 1

    def worker_base_url(self, port: int | None = None) -> str:
        p = self.worker_base_port if port is None else port
        return f"http://127.0.0.1:{p}/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
