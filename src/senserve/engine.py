"""vLLM worker pool with optional sleep-mode switching (lazy one process per model)."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum

import httpx

from senserve.registry import ModelRegistry, ModelSpec, load_registry
from senserve.settings import Settings, get_settings
from senserve import vllm_sleep

logger = logging.getLogger(__name__)


class EngineState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"  # first model load (no prior active worker)
    SWITCHING = "switching"  # change active model
    READY = "ready"
    ERROR = "error"


_LOADING_STATES = frozenset({EngineState.STARTING, EngineState.SWITCHING})


class WorkerState(str, Enum):
    COLD = "cold"
    STARTING = "starting"
    READY = "ready"
    SLEEPING = "sleeping"
    ERROR = "error"


@dataclass
class EngineStatus:
    state: EngineState
    active_model_id: str | None = None
    target_model_id: str | None = None
    message: str = ""
    error: str | None = None


@dataclass
class WorkerInfo:
    model_id: str
    port: int
    state: WorkerState
    pid: int | None = None
    is_sleeping: bool | None = None


class SwitchingError(Exception):
    """Operations blocked while a model switch is in progress."""


class NotReadyError(Exception):
    """No model is loaded and ready."""


class WorkerExitError(RuntimeError):
    """vLLM worker subprocess exited before becoming ready."""

    def __init__(self, model_id: str, exit_code: int | None) -> None:
        self.model_id = model_id
        self.exit_code = exit_code
        code = exit_code if exit_code is not None else "unknown"
        super().__init__(f"vLLM worker for {model_id} exited before ready (code {code})")


@dataclass
class _Worker:
    spec: ModelSpec
    process: subprocess.Popen[bytes] | None
    port: int
    state: WorkerState = WorkerState.COLD


def _vllm_cli_flag(key: str) -> str:
    if "." in key:
        return f"--{key}"
    return f"--{key.replace('_', '-')}"


def _vllm_cmd(spec: ModelSpec, port: int, defaults: dict, *, sleep_enabled: bool) -> list[str]:
    cmd = [
        "vllm",
        "serve",
        spec.source,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--served-model-name",
        spec.id,
    ]
    if sleep_enabled:
        cmd.append("--enable-sleep-mode")
    opts = {**defaults, **spec.vllm}
    for key, val in opts.items():
        flag = _vllm_cli_flag(key)
        if isinstance(val, bool):
            if val:
                cmd.append(flag)
        elif val is not None:
            cmd.extend([flag, str(val)])
    return cmd


def _worker_http_alive(base_url: str) -> bool:
    """True when the worker already serves OpenAI /v1/models (launcher may have exited)."""
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/models", timeout=3.0)
        return r.status_code == 200
    except Exception:  # noqa: BLE001 — probe only
        return False


def _check_process_alive(
    process: subprocess.Popen[bytes] | None,
    model_id: str,
    *,
    base_url: str | None = None,
) -> None:
    if process is None:
        return
    code = process.poll()
    if code is None:
        return
    # vLLM often exits the Popen parent after forking APIServer; HTTP is the source of truth.
    if base_url and _worker_http_alive(base_url):
        return
    raise WorkerExitError(model_id, code)


def _wait_ready(
    base_url: str,
    timeout_s: float,
    *,
    process: subprocess.Popen[bytes] | None = None,
    model_id: str = "worker",
) -> None:
    deadline = time.monotonic() + timeout_s
    url = f"{base_url.rstrip('/')}/models"
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        _check_process_alive(process, model_id, base_url=base_url)
        try:
            r = httpx.get(url, timeout=5.0)
            if r.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001 — poll until timeout
            last_err = exc
        time.sleep(2.0)
    msg = f"vLLM worker did not become ready in {timeout_s}s"
    if last_err:
        msg = f"{msg}: {last_err}"
    raise TimeoutError(msg)


class EngineSupervisor:
    """Lazy pool: one vLLM process per model; single active; sleep/wake on switch."""

    def __init__(
        self,
        registry: ModelRegistry | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._registry = registry or load_registry()
        self._lock = threading.RLock()
        self._workers: dict[str, _Worker] = {}
        self._active_id: str | None = None
        self._state = EngineState.IDLE
        self._target: str | None = None
        self._message = ""
        self._error: str | None = None
        self._switch_cancel_handled = False
        self._port_index = self._build_port_index()

    def _build_port_index(self) -> dict[str, int]:
        enabled = sorted(m.id for m in self._registry.list_enabled())
        base = self._settings.worker_base_port
        return {mid: base + i for i, mid in enumerate(enabled)}

    @property
    def registry(self) -> ModelRegistry:
        return self._registry

    def _port_for(self, model_id: str) -> int:
        if not self._settings.sleep_enabled:
            return self._settings.worker_base_port
        try:
            return self._port_index[model_id]
        except KeyError as exc:
            raise KeyError(f"Unknown model_id: {model_id}") from exc

    def status(self) -> EngineStatus:
        with self._lock:
            return EngineStatus(
                state=self._state,
                active_model_id=self._active_id,
                target_model_id=self._target,
                message=self._message,
                error=self._error,
            )

    def list_workers(self) -> list[WorkerInfo]:
        with self._lock:
            out: list[WorkerInfo] = []
            for mid, w in self._workers.items():
                pid = w.process.pid if w.process and w.process.poll() is None else None
                sleeping = None
                if self._settings.sleep_enabled and w.state != WorkerState.COLD:
                    sleeping = vllm_sleep.is_sleeping(w.port)
                out.append(
                    WorkerInfo(
                        model_id=mid,
                        port=w.port,
                        state=w.state,
                        pid=pid,
                        is_sleeping=sleeping,
                    )
                )
            return out

    def reject_if_switching(self) -> None:
        with self._lock:
            if self._state in _LOADING_STATES:
                raise SwitchingError("Model load in progress")
            if self._state != EngineState.READY or not self._active_id:
                raise NotReadyError("No model loaded")

    def _load_state_for(self, model_id: str) -> EngineState:
        with self._lock:
            prior_active = self._active_id
        if prior_active and prior_active != model_id:
            return EngineState.SWITCHING
        return EngineState.STARTING

    def _begin_load(self, model_id: str) -> EngineState:
        load_state = self._load_state_for(model_id)
        verb = "Switching to" if load_state == EngineState.SWITCHING else "Starting"
        with self._lock:
            if self._state in _LOADING_STATES:
                raise SwitchingError("Model load already in progress")
            self._state = load_state
            self._target = model_id
            self._message = f"{verb} {model_id}..."
            self._error = None
            self._switch_cancel_handled = False
        return load_state

    def backend_base_url(self) -> str:
        self.reject_if_switching()
        with self._lock:
            if not self._active_id:
                raise NotReadyError("No model loaded")
            port = self._workers[self._active_id].port
        return self._settings.worker_base_url(port)

    def load(self, model_id: str) -> None:
        spec = self._registry.get(model_id)
        if not spec.enabled:
            raise ValueError(f"Model {model_id} is disabled in registry")
        with self._lock:
            if self._state in _LOADING_STATES:
                raise SwitchingError("Model load already in progress")
            if self._active_id == model_id and self._state == EngineState.READY:
                return
        self._begin_load(model_id)
        threading.Thread(target=self._load_sync, args=(spec,), daemon=True).start()

    def load_blocking(self, model_id: str) -> None:
        spec = self._registry.get(model_id)
        if not spec.enabled:
            raise ValueError(f"Model {model_id} is disabled in registry")
        with self._lock:
            if self._state in _LOADING_STATES:
                raise SwitchingError("Model load already in progress")
            if self._active_id == model_id and self._state == EngineState.READY:
                return
        self._begin_load(model_id)
        self._load_sync(spec)

    def cancel_switch(self) -> EngineStatus:
        """Abort an in-progress load/switch; kill target worker and restore prior active if possible."""
        with self._lock:
            if self._state not in _LOADING_STATES:
                return self.status()
            target_id = self._target
            rollback_id = self._active_id
            self._switch_cancel_handled = True
            self._target = None
        if target_id:
            self._kill_worker(target_id)
            worker = self._workers.get(target_id)
            if worker:
                worker.state = WorkerState.COLD
        msg = "Switch cancelled" if rollback_id else "Start cancelled"
        self._restore_active_worker(rollback_id, message=msg)
        with self._lock:
            self._switch_cancel_handled = False
            return self.status()

    def _load_sync(self, spec: ModelSpec) -> None:
        rollback_id: str | None
        with self._lock:
            rollback_id = self._active_id if self._active_id != spec.id else None
        try:
            if self._settings.sleep_enabled:
                self._switch_sleep_mode(spec)
            else:
                self._switch_kill_restart(spec)
            with self._lock:
                if self._switch_cancel_handled:
                    return
                self._active_id = spec.id
                self._state = EngineState.READY
                self._target = None
                self._message = f"Loaded {spec.id}"
                self._error = None
                logger.info("Model %s ready on port %s", spec.id, self._port_for(spec.id))
        except Exception as exc:
            logger.exception("Failed to load model %s", spec.id)
            with self._lock:
                if self._switch_cancel_handled:
                    return
            self._handle_failed_switch(spec.id, rollback_id, exc)

    def _handle_failed_switch(
        self,
        target_id: str,
        rollback_id: str | None,
        exc: Exception,
    ) -> None:
        with self._lock:
            if self._switch_cancel_handled:
                return
        self._kill_worker(target_id)
        worker = self._workers.get(target_id)
        if worker:
            worker.state = WorkerState.COLD
        self._restore_active_worker(
            rollback_id,
            message=f"Failed to load {target_id}",
            error=str(exc),
        )

    def _restore_active_worker(
        self,
        model_id: str | None,
        *,
        message: str,
        error: str | None = None,
    ) -> None:
        if not model_id:
            with self._lock:
                self._active_id = None
                self._state = EngineState.IDLE
                self._target = None
                self._message = message
                self._error = error
            return

        worker = self._workers.get(model_id)
        if worker is None:
            with self._lock:
                self._active_id = model_id
                self._state = EngineState.ERROR
                self._target = None
                self._message = message
                self._error = error or "Worker not in pool"
            return

        try:
            if worker.state == WorkerState.SLEEPING:
                worker.state = WorkerState.STARTING
                vllm_sleep.wake_worker(
                    worker.port,
                    self._settings.sleep_level,
                    self._settings.worker_ready_timeout_s,
                )
                _wait_ready(
                    self._settings.worker_base_url(worker.port),
                    self._settings.worker_ready_timeout_s,
                    process=worker.process,
                    model_id=model_id,
                )
                worker.state = WorkerState.READY
            elif worker.state != WorkerState.READY:
                raise RuntimeError(f"Worker {model_id} in state {worker.state.value}")
            with self._lock:
                self._active_id = model_id
                self._state = EngineState.READY
                self._target = None
                self._message = message
                self._error = error
        except Exception as restore_exc:
            logger.exception("Failed to restore model %s after switch error", model_id)
            with self._lock:
                self._active_id = model_id
                self._state = EngineState.ERROR
                self._target = None
                self._message = message
                self._error = error or str(restore_exc)

    def _switch_kill_restart(self, spec: ModelSpec) -> None:
        """Legacy path: terminate active worker and spawn a new vLLM on the single port."""
        self._kill_worker(self._active_id)
        port = self._settings.worker_base_port
        worker = self._start_worker(spec, port)
        with self._lock:
            self._workers = {spec.id: worker}
        _wait_ready(
            self._settings.worker_base_url(port),
            self._settings.worker_ready_timeout_s,
            process=worker.process,
            model_id=spec.id,
        )
        with self._lock:
            worker.state = WorkerState.READY

    def _switch_sleep_mode(self, spec: ModelSpec) -> None:
        active_id = self._active_id
        if active_id and active_id != spec.id:
            self._sleep_active(active_id)

        worker = self._workers.get(spec.id)
        port = self._port_for(spec.id)

        if worker is None or worker.state == WorkerState.COLD:
            if worker is None:
                worker = _Worker(spec=spec, process=None, port=port, state=WorkerState.COLD)
                with self._lock:
                    self._workers[spec.id] = worker
            self._start_and_wait(worker)
        elif worker.state == WorkerState.SLEEPING:
            worker.state = WorkerState.STARTING
            vllm_sleep.wake_worker(port, self._settings.sleep_level, self._settings.worker_ready_timeout_s)
            _wait_ready(
                self._settings.worker_base_url(port),
                self._settings.worker_ready_timeout_s,
                process=worker.process,
                model_id=spec.id,
            )
            worker.state = WorkerState.READY
        elif worker.state in (WorkerState.READY, WorkerState.STARTING):
            if worker.process and worker.process.poll() is not None:
                self._start_and_wait(worker)
        else:
            raise RuntimeError(f"Worker {spec.id} in unexpected state {worker.state}")

    def _sleep_active(self, active_id: str) -> None:
        worker = self._workers.get(active_id)
        if not worker or worker.state != WorkerState.READY:
            return
        vllm_sleep.sleep_worker(worker.port, level=self._settings.sleep_level)
        worker.state = WorkerState.SLEEPING

    def _start_and_wait(self, worker: _Worker) -> None:
        if worker.process and worker.process.poll() is None:
            pass
        else:
            proc = self._spawn(worker.spec, worker.port)
            worker.process = proc
        worker.state = WorkerState.STARTING
        _wait_ready(
            self._settings.worker_base_url(worker.port),
            self._settings.worker_ready_timeout_s,
            process=worker.process,
            model_id=worker.spec.id,
        )
        worker.state = WorkerState.READY

    def _start_worker(self, spec: ModelSpec, port: int) -> _Worker:
        proc = self._spawn(spec, port)
        return _Worker(spec=spec, process=proc, port=port, state=WorkerState.STARTING)

    def _spawn(self, spec: ModelSpec, port: int) -> subprocess.Popen[bytes]:
        cmd = _vllm_cmd(spec, port, {}, sleep_enabled=self._settings.sleep_enabled)
        env = os.environ.copy()
        if self._settings.sleep_enabled:
            env["VLLM_SERVER_DEV_MODE"] = "1"
        logger.info("Starting vLLM: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd, env=env)  # noqa: S603
        return proc

    def _kill_worker(self, model_id: str | None) -> None:
        if not model_id:
            return
        worker = self._workers.get(model_id)
        if not worker or not worker.process:
            return
        proc = worker.process
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        worker.state = WorkerState.COLD
        worker.process = None

    def active_model_id(self) -> str | None:
        with self._lock:
            return self._active_id

    def reload_registry(self) -> None:
        """Reload catalog from disk; unload if active model removed or disabled."""
        with self._lock:
            if self._state in _LOADING_STATES:
                raise SwitchingError("Model load in progress")
            active = self._active_id
            new_reg = load_registry()
            self._registry = new_reg
            self._port_index = self._build_port_index()
            for mid in list(self._workers):
                if mid not in new_reg.models:
                    self._kill_worker(mid)
                    del self._workers[mid]
            if active:
                try:
                    spec = new_reg.get(active)
                except KeyError:
                    spec = None
                if spec is None or not spec.enabled:
                    self._kill_worker(active)
                    self._active_id = None
                    if self._state == EngineState.READY:
                        self._state = EngineState.IDLE
                        self._message = "Active model removed from catalog"
