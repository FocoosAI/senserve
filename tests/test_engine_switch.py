"""Engine switch failure handling and cancel."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from senserve.engine import (
    EngineState,
    EngineSupervisor,
    LoadAbortedError,
    WorkerExitError,
    WorkerState,
    _Worker,
    _check_process_alive,
    _wait_ready,
)
from senserve import vllm_sleep
from senserve.registry import ModelRegistry, ModelSpec


def _spec(model_id: str) -> ModelSpec:
    return ModelSpec(
        id=model_id,
        display_name=model_id,
        source=f"hf/{model_id}",
        capabilities=frozenset({"text"}),
        enabled=True,
    )


def _registry(*ids: str) -> ModelRegistry:
    return ModelRegistry(models={i: _spec(i) for i in ids})


def test_check_process_alive_raises_on_exit():
    proc = MagicMock(spec=subprocess.Popen)
    proc.poll.return_value = 1
    with pytest.raises(WorkerExitError) as exc_info:
        _check_process_alive(proc, "m1")
    assert exc_info.value.model_id == "m1"
    assert exc_info.value.exit_code == 1


def test_check_process_alive_ignores_exit_when_http_up():
    proc = MagicMock(spec=subprocess.Popen)
    proc.poll.return_value = 1
    with patch("senserve.engine._worker_http_alive", return_value=True):
        _check_process_alive(proc, "m1", base_url="http://127.0.0.1:8001")


def test_wait_ready_fails_fast_when_process_exits():
    proc = MagicMock(spec=subprocess.Popen)
    proc.poll.return_value = 137

    def fake_get(url, **kwargs):
        raise OSError("connection refused")

    with patch("senserve.engine.httpx.get", side_effect=fake_get):
        with pytest.raises(WorkerExitError):
            _wait_ready("http://127.0.0.1:8000", timeout_s=30.0, process=proc, model_id="m1")


def test_begin_load_starting_without_prior_active():
    reg = _registry("a")
    sup = EngineSupervisor(registry=reg)
    sup._begin_load("a")
    st = sup.status()
    assert st.state == EngineState.STARTING
    assert st.target_model_id == "a"


def test_begin_load_switching_with_prior_active():
    reg = _registry("a", "b")
    sup = EngineSupervisor(registry=reg)
    sup._active_id = "a"
    sup._state = EngineState.READY
    sup._begin_load("b")
    st = sup.status()
    assert st.state == EngineState.SWITCHING
    assert st.target_model_id == "b"


def test_cancel_switch_restores_prior_active():
    reg = _registry("a", "b")
    sup = EngineSupervisor(registry=reg)
    sup._workers["a"] = _Worker(spec=_spec("a"), process=None, port=8000, state=WorkerState.SLEEPING)
    sup._workers["b"] = _Worker(spec=_spec("b"), process=None, port=8001, state=WorkerState.STARTING)
    sup._active_id = "a"
    sup._state = EngineState.SWITCHING
    sup._target = "b"

    with (
        patch.object(sup, "_kill_worker"),
        patch("senserve.engine.vllm_sleep.wake_worker"),
        patch("senserve.engine._wait_ready"),
    ):
        st = sup.cancel_switch()

    assert st.state.value == "ready"
    assert st.active_model_id == "a"
    assert st.target_model_id is None
    assert sup._workers["b"].state == WorkerState.COLD


def test_failed_switch_kills_target_and_restores_active():
    reg = _registry("a", "b")
    sup = EngineSupervisor(registry=reg)
    sup._workers["a"] = _Worker(spec=_spec("a"), process=None, port=8000, state=WorkerState.READY)
    sup._workers["b"] = _Worker(spec=_spec("b"), process=None, port=8001, state=WorkerState.STARTING)
    sup._active_id = "a"
    sup._state = EngineState.SWITCHING
    sup._target = "b"

    with patch.object(sup, "_kill_worker") as kill:
        sup._handle_failed_switch("b", "a", WorkerExitError("b", 1))

    assert kill.call_count >= 1
    st = sup.status()
    assert st.active_model_id == "a"
    assert st.state.value == "ready"
    assert st.error is not None
    assert "exited" in st.error


def test_sleep_failure_kills_active_and_continues_switch():
    reg = _registry("a", "b")
    sup = EngineSupervisor(registry=reg)
    sup._workers["a"] = _Worker(spec=_spec("a"), process=None, port=8000, state=WorkerState.READY)
    sup._active_id = "a"
    sup._state = EngineState.SWITCHING
    sup._target = "b"

    with (
        patch("senserve.engine.vllm_sleep.sleep_worker", side_effect=vllm_sleep.VllmSleepError("timed out")),
        patch.object(sup, "_kill_worker") as kill,
        patch.object(sup, "_start_and_wait"),
    ):
        sup._switch_sleep_mode(_spec("b"))

    kill.assert_called_once_with("a")


def test_sleep_cancel_raises_load_aborted():
    reg = _registry("a", "b")
    sup = EngineSupervisor(registry=reg)
    sup._workers["a"] = _Worker(spec=_spec("a"), process=None, port=8000, state=WorkerState.READY)
    sup._load_abort.set()

    with (
        patch(
            "senserve.engine.vllm_sleep.sleep_worker",
            side_effect=vllm_sleep.VllmSleepError("sleep cancelled on port 8000"),
        ),
        patch.object(sup, "_kill_worker") as kill,
    ):
        with pytest.raises(LoadAbortedError):
            sup._sleep_active("a")

    kill.assert_not_called()


def test_load_sync_ignores_errors_after_cancel_abort():
    reg = _registry("a", "b")
    sup = EngineSupervisor(registry=reg)
    sup._workers["a"] = _Worker(spec=_spec("a"), process=None, port=8000, state=WorkerState.READY)
    sup._active_id = "a"
    sup._state = EngineState.SWITCHING
    sup._target = "b"
    sup._load_abort.set()

    with patch.object(sup, "_switch_sleep_mode", side_effect=RuntimeError("late failure")):
        sup._load_sync(_spec("b"))

    assert sup.status().state == EngineState.SWITCHING
