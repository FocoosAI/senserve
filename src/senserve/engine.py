"""vLLM worker subprocess + single-active supervisor (A1; extensible to multi-worker)."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum

import httpx

from senserve.registry import ModelRegistry, ModelSpec, load_registry
from senserve.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class EngineState(str, Enum):
    IDLE = "idle"
    SWITCHING = "switching"
    READY = "ready"
    ERROR = "error"


@dataclass
class EngineStatus:
    state: EngineState
    active_model_id: str | None = None
    target_model_id: str | None = None
    message: str = ""
    error: str | None = None


class SwitchingError(Exception):
    """Operations blocked while a model switch is in progress."""


class NotReadyError(Exception):
    """No model is loaded and ready."""


@dataclass
class _Worker:
    spec: ModelSpec
    process: subprocess.Popen[bytes]
    port: int


def _vllm_cli_flag(key: str) -> str:
    """Map models.toml key to vLLM CLI flag (supports dotted flags)."""
    if "." in key:
        return f"--{key}"
    return f"--{key.replace('_', '-')}"


def _vllm_cmd(spec: ModelSpec, port: int, defaults: dict) -> list[str]:
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
    """Manages one vLLM worker at a time (placement policy: single_active)."""

    def __init__(
        self,
        registry: ModelRegistry | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._registry = registry or load_registry()
        self._lock = threading.RLock()
        self._worker: _Worker | None = None
        self._state = EngineState.IDLE
        self._target: str | None = None
        self._message = ""
        self._error: str | None = None

    @property
    def registry(self) -> ModelRegistry:
        return self._registry

    def status(self) -> EngineStatus:
        with self._lock:
            active = self._worker.spec.id if self._worker else None
            return EngineStatus(
                state=self._state,
                active_model_id=active,
                target_model_id=self._target,
                message=self._message,
                error=self._error,
            )

    def reject_if_switching(self) -> None:
        with self._lock:
            if self._state == EngineState.SWITCHING:
                raise SwitchingError("Model switch in progress")
            if self._state != EngineState.READY or not self._worker:
                raise NotReadyError("No model loaded")

    def backend_base_url(self) -> str:
        self.reject_if_switching()
        return self._settings.worker_base_url()

    def load(self, model_id: str) -> None:
        """Load model_id in a background thread (unloads previous worker)."""
        spec = self._registry.get(model_id)
        if not spec.enabled:
            raise ValueError(f"Model {model_id} is disabled in registry")
        with self._lock:
            if self._state == EngineState.SWITCHING:
                raise SwitchingError("Model switch already in progress")
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
            self._state = EngineState.SWITCHING
            self._target = model_id
            self._message = f"Switching to {model_id}..."
            self._error = None
        self._load_sync(spec)

    def _load_sync(self, spec: ModelSpec) -> None:
        try:
            self._stop_worker()
            port = self._settings.worker_port
            cmd = _vllm_cmd(spec, port, {})
            logger.info("Starting vLLM: %s", " ".join(cmd))
            proc = subprocess.Popen(cmd)  # noqa: S603 — intentional vllm spawn
            worker = _Worker(spec=spec, process=proc, port=port)
            _wait_ready(
                f"http://127.0.0.1:{port}/v1",
                self._settings.worker_ready_timeout_s,
            )
            with self._lock:
                self._worker = worker
                self._state = EngineState.READY
                self._target = None
                self._message = f"Loaded {spec.id}"
                logger.info("Model %s ready on port %s", spec.id, port)
        except Exception as exc:
            logger.exception("Failed to load model %s", spec.id)
            self._stop_worker()
            with self._lock:
                self._state = EngineState.ERROR
                self._target = None
                self._message = f"Failed to load {spec.id}"
                self._error = str(exc)

    def _stop_worker(self) -> None:
        worker = self._worker
        self._worker = None
        if not worker:
            return
        proc = worker.process
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)

    def active_model_id(self) -> str | None:
        with self._lock:
            return self._worker.spec.id if self._worker else None
