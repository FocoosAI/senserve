"""Internal HTTP client for vLLM sleep/wake admin endpoints (localhost only)."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class VllmSleepError(Exception):
    """vLLM sleep/wake API call failed."""


def _root_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def is_sleeping(port: int, timeout: float = 10.0) -> bool | None:
    """Return sleep state, or None if endpoint unavailable."""
    try:
        r = httpx.get(f"{_root_url(port)}/is_sleeping", timeout=timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        if isinstance(data, bool):
            return data
        if isinstance(data, dict):
            return bool(data.get("is_sleeping", data.get("sleeping")))
        return r.text.strip().lower() in ("true", "1")
    except httpx.HTTPError:
        return None


def sleep_worker(port: int, level: int = 2, timeout: float = 120.0) -> None:
    """Put a vLLM worker to sleep (frees GPU memory, keeps process alive)."""
    try:
        r = httpx.post(
            f"{_root_url(port)}/sleep",
            params={"level": level},
            timeout=timeout,
        )
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise VllmSleepError(f"sleep failed on port {port}: {exc}") from exc
    logger.info("vLLM worker on port %s sleeping (level=%s)", port, level)


def wake_worker_l1(port: int, timeout: float = 600.0) -> None:
    """Wake a worker from level-1 sleep (weights in CPU RAM)."""
    try:
        r = httpx.post(f"{_root_url(port)}/wake_up", timeout=timeout)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise VllmSleepError(f"wake_up (L1) failed on port {port}: {exc}") from exc
    logger.info("vLLM worker on port %s awake (level 1)", port)


def wake_worker_l2(port: int, timeout: float = 600.0) -> None:
    """Wake a worker from level-2 sleep (reload weights + KV cache)."""
    base = _root_url(port)
    steps: list[tuple[str, str, dict]] = [
        ("POST", f"{base}/wake_up", {"params": {"tags": "weights"}}),
        (
            "POST",
            f"{base}/collective_rpc",
            {"json": {"method": "reload_weights"}, "timeout": timeout},
        ),
        ("POST", f"{base}/wake_up", {"params": {"tags": "kv_cache"}}),
        ("POST", f"{base}/reset_prefix_cache", {}),
    ]
    try:
        with httpx.Client(timeout=timeout) as client:
            for method, url, kwargs in steps:
                r = client.request(method, url, **kwargs)
                r.raise_for_status()
    except httpx.HTTPError as exc:
        raise VllmSleepError(f"wake (L2) failed on port {port}: {exc}") from exc
    logger.info("vLLM worker on port %s awake (level 2)", port)


def wake_worker(port: int, level: int, timeout: float = 600.0) -> None:
    if level >= 2:
        wake_worker_l2(port, timeout=timeout)
    else:
        wake_worker_l1(port, timeout=timeout)
