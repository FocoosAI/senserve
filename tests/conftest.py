from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from senserve import vllm_flags
from senserve.engine import EngineState, EngineSupervisor, WorkerState, _Worker
from senserve.gateway.app import create_app

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def vllm_flags_preloaded():
    """Avoid subprocess vllm --help during TestClient lifespan."""
    prev = vllm_flags._vllm_flags
    vllm_flags._vllm_flags = []
    yield
    vllm_flags._vllm_flags = prev


@pytest.fixture
def models_config(tmp_path: Path) -> Path:
    path = tmp_path / "models.yaml"
    path.write_text(
        (FIXTURES / "models.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def registry(models_config):
    from senserve.registry import load_registry

    return load_registry(models_config)


@pytest.fixture
def supervisor_ready(registry):
    spec = registry.get("qwen3-vl-4b-awq")
    sup = EngineSupervisor(registry=registry)
    sup._state = EngineState.READY  # noqa: SLF001 — test double
    sup._active_id = spec.id  # noqa: SLF001
    sup._workers = {  # noqa: SLF001
        spec.id: _Worker(spec=spec, process=None, port=8000, state=WorkerState.READY),
    }
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
