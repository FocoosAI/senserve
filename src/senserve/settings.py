"""Application settings (env overrides config file paths)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SENSERVE_", extra="ignore")

    api_port: int = 8787
    worker_port: int = 8000
    models_path: Path = Field(default=_REPO_ROOT / "config" / "models.toml")
    default_model_id: str = "qwen3.5-0.8b"
    max_upload_bytes: int = 300 * 1024 * 1024
    video_max_frames: int = 16
    temp_dir: Path = Field(default=Path("/tmp/senserve"))
    worker_ready_timeout_s: float = 600.0
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:8788", "http://127.0.0.1:8788"]
    )

    def worker_base_url(self) -> str:
        return f"http://127.0.0.1:{self.worker_port}/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
