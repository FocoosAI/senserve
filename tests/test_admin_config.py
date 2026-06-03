from __future__ import annotations

import yaml

from senserve.catalog_config import ConfigDocument, ModelEntry, save_config_document
from senserve.engine import EngineState, EngineSupervisor, WorkerState, _Worker
from senserve.gateway.app import create_app
from senserve.registry import load_registry


def test_get_config(models_config, monkeypatch):
    monkeypatch.setenv("SENSERVE_MODELS_PATH", str(models_config))
    from senserve.settings import get_settings
    from fastapi.testclient import TestClient

    get_settings.cache_clear()
    reg = load_registry(models_config)
    client = TestClient(create_app(EngineSupervisor(registry=reg)))
    resp = client.get("/v1/admin/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "defaults" in data
    assert len(data["models"]) == 2


def test_put_config_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "models.yaml"
    path.write_text(
        (yaml.safe_dump({"defaults": {"worker_port": 8000}, "models": []})),
        encoding="utf-8",
    )
    monkeypatch.setenv("SENSERVE_MODELS_PATH", str(path))
    from senserve.settings import get_settings

    get_settings.cache_clear()

    doc = ConfigDocument(
        defaults={"worker_port": 8000, "gpu_memory_utilization": 0.3},
        models=[
            ModelEntry(
                id="test-model",
                source="hf/test",
                capabilities=["text"],
                default=True,
            )
        ],
    )
    save_config_document(doc, path)
    reg = load_registry(path)
    assert "test-model" in reg.models
    assert reg.get("test-model").vllm["gpu_memory_utilization"] == 0.3


def test_put_config_409_while_switching(client_switching, models_config, monkeypatch):
    monkeypatch.setenv("SENSERVE_MODELS_PATH", str(models_config))
    from senserve.settings import get_settings

    get_settings.cache_clear()
    body = {
        "defaults": {},
        "models": [
            {
                "id": "qwen3-vl-4b-awq",
                "source": "Qwen/Qwen3-VL-4B-Instruct",
                "capabilities": ["text"],
            }
        ],
    }
    resp = client_switching.put("/v1/admin/config", json=body)
    assert resp.status_code == 409


def test_put_config_409_affects_active_model(models_config, monkeypatch):
    monkeypatch.setenv("SENSERVE_MODELS_PATH", str(models_config))
    from senserve.settings import get_settings

    get_settings.cache_clear()
    reg = load_registry(models_config)
    sup = EngineSupervisor(registry=reg)
    sup._state = EngineState.READY  # noqa: SLF001
    sup._active_id = "qwen3-vl-4b-awq"  # noqa: SLF001
    sup._workers = {  # noqa: SLF001
        "qwen3-vl-4b-awq": _Worker(
            spec=reg.get("qwen3-vl-4b-awq"),
            process=None,
            port=8000,
            state=WorkerState.READY,
        ),
    }
    client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(
        create_app(sup)
    )
    body = {
        "defaults": {},
        "models": [
            {
                "id": "qwen3-vl-4b-awq",
                "source": "changed/source",
                "capabilities": ["text", "vision", "video"],
                "enabled": True,
            },
            {
                "id": "gemma-4-26b-a4b-it",
                "source": "google/gemma",
                "capabilities": ["text", "image"],
                "enabled": True,
            },
        ],
    }
    resp = client.put("/v1/admin/config", json=body)
    assert resp.status_code == 409
