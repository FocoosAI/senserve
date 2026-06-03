from senserve.engine import EngineState, EngineSupervisor, WorkerState, _Worker
from senserve.registry import load_registry


def test_reload_registry_unloads_removed_active(models_config, monkeypatch):
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

    # Simulate catalog reload without active model (patch registry in place)
    from senserve import catalog_config

    doc = catalog_config.load_config_document(models_config)
    doc["models"] = [m for m in doc["models"] if m["id"] != "qwen3-vl-4b-awq"]
    catalog_config.save_config_document(
        catalog_config.ConfigDocument(
            defaults=doc.get("defaults", {}),
            models=doc["models"],
        ),
        models_config,
    )

    sup.reload_registry()
    assert sup.active_model_id() is None
    assert sup.status().state == EngineState.IDLE
