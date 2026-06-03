"""Engine supervisor sleep-mode switching (mocked vLLM HTTP)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from senserve.engine import EngineSupervisor, WorkerState


@pytest.fixture
def sleep_settings(models_config, monkeypatch):
    monkeypatch.setenv("SENSERVE_SLEEP_MODE", "level2")
    monkeypatch.setenv("SENSERVE_MODELS_PATH", str(models_config))
    from senserve.settings import get_settings

    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def registry(models_config):
    from senserve.registry import load_registry

    return load_registry(models_config)


def test_vllm_cmd_includes_sleep_flag(registry, sleep_settings):
    from senserve.engine import _vllm_cmd

    spec = registry.get("qwen3-vl-4b-awq")
    cmd = _vllm_cmd(spec, 8000, {}, sleep_enabled=True)
    assert "--enable-sleep-mode" in cmd


def test_port_index_per_model(registry, sleep_settings):
    sup = EngineSupervisor(registry=registry, settings=sleep_settings)
    ports = {mid: sup._port_for(mid) for mid in sorted(registry.models)}
    assert len(set(ports.values())) == len(ports)
    assert ports["qwen3-vl-4b-awq"] == ports["gemma-4-26b-a4b-it"] + 1


@patch("senserve.engine._wait_ready")
@patch("senserve.engine.subprocess.Popen")
@patch("senserve.vllm_sleep.wake_worker")
@patch("senserve.vllm_sleep.sleep_worker")
def test_switch_sleep_wake(
    mock_sleep,
    mock_wake,
    mock_popen,
    mock_wait,
    registry,
    sleep_settings,
):
    mock_popen.return_value = MagicMock(pid=99, poll=MagicMock(return_value=None))
    sup = EngineSupervisor(registry=registry, settings=sleep_settings)

    spec_a = registry.get("qwen3-vl-4b-awq")
    spec_b = registry.get("gemma-4-26b-a4b-it")

    with patch.object(sup, "_spawn", return_value=mock_popen.return_value):
        sup._load_sync(spec_a)
    assert sup.active_model_id() == spec_a.id
    mock_sleep.assert_not_called()

    sup._load_sync(spec_b)
    mock_sleep.assert_called_once()
    mock_wake.assert_not_called()
    assert sup.active_model_id() == spec_b.id
    assert sup._workers[spec_a.id].state == WorkerState.SLEEPING

    sup._load_sync(spec_a)
    assert mock_sleep.call_count == 2
    mock_wake.assert_called_once()
    assert sup.active_model_id() == spec_a.id
