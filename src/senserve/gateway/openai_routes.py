from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from senserve.engine import EngineState, EngineStatus, EngineSupervisor, NotReadyError, SwitchingError, WorkerInfo
from senserve.gateway.errors import openai_error_response, switching_response
from senserve.preprocessors import CapabilityError, preprocess_messages

logger = logging.getLogger(__name__)


def _catalog_model_status(
    model_id: str,
    workers_by_id: dict[str, WorkerInfo],
    st: EngineStatus,
) -> str:
    """Per-model worker state for /v1/models (Senserve extension, not OpenAI core)."""
    worker = workers_by_id.get(model_id)
    if worker is not None:
        return worker.state.value
    if st.state == EngineState.SWITCHING and st.target_model_id == model_id:
        return "starting"
    return "cold"


def create_openai_router(supervisor: EngineSupervisor) -> APIRouter:
    router = APIRouter(prefix="/v1")

    @router.get("/models")
    def list_models():
        """OpenAI-compatible model list (for Open WebUI / SDK model picker)."""
        active = supervisor.active_model_id()
        st = supervisor.status()
        ready = st.state == EngineState.READY
        workers_by_id = {w.model_id: w for w in supervisor.list_workers()}
        now = int(time.time())
        data = []
        for i, spec in enumerate(supervisor.registry.list_enabled()):
            is_loaded = spec.id == active and ready
            data.append(
                {
                    "id": spec.id,
                    "object": "model",
                    "created": now - i,
                    "owned_by": "senserve",
                    # Open WebUI shows id in the selector; name helps humans.
                    "name": spec.display_name,
                    # Senserve extensions (OpenAI Model object is id/object/created/owned_by only).
                    "source": spec.source,
                    "status": _catalog_model_status(spec.id, workers_by_id, st),
                    "loaded": is_loaded,
                    "capabilities": sorted(spec.capabilities),
                }
            )
        return {"object": "list", "data": data}

    @router.post("/chat/completions")
    async def chat_completions(request: Request):
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return openai_error_response("Invalid JSON body")

        model_id = body.get("model")
        if not model_id:
            return openai_error_response("model is required")

        try:
            spec = supervisor.registry.get(model_id)
        except KeyError:
            return openai_error_response(f"Unknown model: {model_id}", status_code=404)

        st = supervisor.status()
        if st.state == EngineState.SWITCHING:
            return switching_response(f"Loading model {st.target_model_id}; retry shortly")

        active = supervisor.active_model_id()
        if active != model_id:
            if st.state != EngineState.SWITCHING:
                supervisor.load(model_id)
            return switching_response(f"Loading model {model_id}; retry shortly")

        try:
            supervisor.reject_if_switching()
        except SwitchingError:
            return switching_response()
        except NotReadyError:
            return openai_error_response("No model loaded", error_type="server_error", status_code=503)

        messages = body.get("messages")
        if not messages:
            return openai_error_response("messages is required")

        try:
            body = dict(body)
            body["messages"] = await asyncio.to_thread(preprocess_messages, messages, spec)
        except CapabilityError as exc:
            return openai_error_response(str(exc))
        except ValueError as exc:
            return openai_error_response(str(exc))

        stream = bool(body.get("stream"))
        base = supervisor.backend_base_url()
        if stream:
            return await _proxy_stream(base, body, request)
        return await _proxy_json(base, body)

    return router


async def _proxy_json(base: str, body: dict[str, Any]) -> JSONResponse:
    async with httpx.AsyncClient(timeout=None) as client:
        r = await client.post(f"{base}/chat/completions", json=body)
    ct = r.headers.get("content-type", "application/json")
    return JSONResponse(content=r.json(), status_code=r.status_code, media_type=ct)


async def _proxy_stream(
    base: str, body: dict[str, Any], request: Request
) -> StreamingResponse:
    client = httpx.AsyncClient(timeout=None)

    async def generate():
        try:
            async with client.stream(
                "POST", f"{base}/chat/completions", json=body
            ) as r:
                async for chunk in r.aiter_bytes():
                    if await request.is_disconnected():
                        break
                    yield chunk
        finally:
            await client.aclose()

    return StreamingResponse(generate(), media_type="text/event-stream")
