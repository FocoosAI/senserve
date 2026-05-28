from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from senserve.engine import EngineState, EngineSupervisor
from senserve.gateway.app import create_app

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def models_toml(tmp_path: Path) -> Path:
    path = tmp_path / "models.toml"
    path.write_text(
        (FIXTURES / "models.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def registry(models_toml):
    from senserve.registry import load_registry

    return load_registry(models_toml)


@pytest.fixture
def supervisor_ready(registry):
    sup = EngineSupervisor(registry=registry)
    sup._state = EngineState.READY  # noqa: SLF001 — test double
    sup._worker = type(  # noqa: SLF001
        "W", (), {"spec": registry.get("qwen3-vl-4b-awq")}
    )()
    return sup


@pytest.fixture
def supervisor_switching(registry):
    sup = EngineSupervisor(registry=registry)
    sup._state = EngineState.SWITCHING  # noqa: SLF001
    sup._target = "qwen3-vl-4b-awq"  # noqa: SLF001
    return sup


@pytest.fixture
def client_ready(supervisor_ready):
    return TestClient(create_app(supervisor_ready))


@pytest.fixture
def client_switching(supervisor_switching):
    return TestClient(create_app(supervisor_switching))
