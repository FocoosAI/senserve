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
    SWITCHING = "switching"
    READY = "ready"
    ERROR = "error"


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


def _wait_ready(base_url: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    url = f"{base_url.rstrip('/')}/models"
    last_err: Exception | None = None
    while time.monotonic() < deadline:
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
            if self._state == EngineState.SWITCHING:
                raise SwitchingError("Model switch in progress")
            if self._state != EngineState.READY or not self._active_id:
                raise NotReadyError("No model loaded")

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
            if self._state == EngineState.SWITCHING:
                raise SwitchingError("Model switch already in progress")
            if self._active_id == model_id and self._state == EngineState.READY:
                return
            self._state = EngineState.SWITCHING
            self._target = model_id
            self._message = f"Switching to {model_id}..."
            self._error = None
        threading.Thread(target=self._load_sync, args=(spec,), daemon=True).start()

    def load_blocking(self, model_id: str) -> None:
        spec = self._registry.get(model_id)
        if not spec.enabled:
            raise ValueError(f"Model {model_id} is disabled in registry")
        with self._lock:
            if self._state == EngineState.SWITCHING:
                raise SwitchingError("Model switch already in progress")
            if self._active_id == model_id and self._state == EngineState.READY:
                return
            self._state = EngineState.SWITCHING
            self._target = model_id
            self._message = f"Switching to {model_id}..."
            self._error = None
        self._load_sync(spec)

    def _load_sync(self, spec: ModelSpec) -> None:
        try:
            if self._settings.sleep_enabled:
                self._switch_sleep_mode(spec)
            else:
                self._switch_kill_restart(spec)
            with self._lock:
                self._active_id = spec.id
                self._state = EngineState.READY
                self._target = None
                self._message = f"Loaded {spec.id}"
                logger.info("Model %s ready on port %s", spec.id, self._port_for(spec.id))
        except Exception as exc:
            logger.exception("Failed to load model %s", spec.id)
            with self._lock:
                self._state = EngineState.ERROR
                self._target = None
                self._message = f"Failed to load {spec.id}"
                self._error = str(exc)

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
        return subprocess.Popen(cmd, env=env)  # noqa: S603

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
            if self._state == EngineState.SWITCHING:
                raise SwitchingError("Model switch in progress")
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
