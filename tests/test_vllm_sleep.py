"""Tests for vLLM sleep/wake HTTP client."""

from __future__ import annotations

import threading
import time

import httpx
import pytest
import respx

from senserve.vllm_sleep import VllmSleepError, sleep_worker, wake_worker_l2


@respx.mock
def test_sleep_worker_posts_level():
    route = respx.post("http://127.0.0.1:8010/sleep").mock(return_value=httpx.Response(200))
    sleep_worker(8010, level=2)
    assert route.called
    assert route.calls[0].request.url.params["level"] == "2"


@respx.mock
def test_wake_worker_l2_sequence():
    respx.post("http://127.0.0.1:8010/wake_up").mock(return_value=httpx.Response(200))
    respx.post("http://127.0.0.1:8010/collective_rpc").mock(return_value=httpx.Response(200))
    respx.post("http://127.0.0.1:8010/reset_prefix_cache").mock(return_value=httpx.Response(200))
    wake_worker_l2(8010)
    assert len(respx.calls) == 4


@respx.mock
def test_sleep_worker_raises_on_failure():
    respx.post("http://127.0.0.1:8010/sleep").mock(return_value=httpx.Response(500))
    with pytest.raises(VllmSleepError):
        sleep_worker(8010)


@respx.mock
def test_sleep_worker_retries_read_timeout_then_succeeds():
    route = respx.post("http://127.0.0.1:8010/sleep").mock(
        side_effect=[
            httpx.ReadTimeout("slow"),
            httpx.Response(200),
        ]
    )
    sleep_worker(8010, timeout=30.0, poll_interval_s=1.0)
    assert route.call_count == 2


@respx.mock
def test_sleep_worker_cancelled_between_chunks():
    cancel = threading.Event()
    route = respx.post("http://127.0.0.1:8010/sleep").mock(side_effect=httpx.ReadTimeout("slow"))

    def set_cancel() -> None:
        time.sleep(0.05)
        cancel.set()

    threading.Thread(target=set_cancel, daemon=True).start()
    with pytest.raises(VllmSleepError, match="cancelled"):
        sleep_worker(8010, timeout=10.0, poll_interval_s=0.02, cancel_event=cancel)
    assert route.call_count >= 1
