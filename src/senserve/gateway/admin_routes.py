from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from senserve.catalog_config import (
    ConfigDocument,
    config_affects_active_model,
    load_config_document,
    load_local_overlay,
    models_path,
    preview_registry,
    save_config_document,
)
from senserve.engine import EngineState, EngineSupervisor, SwitchingError
from senserve import vllm_flags


class LoadModelRequest(BaseModel):
    model_id: str


def create_admin_router(supervisor: EngineSupervisor) -> APIRouter:
    router = APIRouter(prefix="/v1/admin")

    @router.get("/models/status")
    def models_status():
        s = supervisor.status()
        workers = [
            {
                "model_id": w.model_id,
                "port": w.port,
                "state": w.state.value,
                "pid": w.pid,
                "is_sleeping": w.is_sleeping,
            }
            for w in supervisor.list_workers()
        ]
        return {
            "state": s.state.value,
            "active_model_id": s.active_model_id,
            "target_model_id": s.target_model_id,
            "message": s.message,
            "error": s.error,
            "workers": workers,
        }

    @router.get("/config")
    def get_config():
        raw = load_config_document()
        doc = ConfigDocument(
            defaults=raw.get("defaults") or {},
            models=raw.get("models") or [],
        )
        local = load_local_overlay()
        return {
            "path": str(models_path()),
            "defaults": doc.defaults,
            "models": [m.model_dump() for m in doc.models],
            "local_overlay": local,
        }

    @router.put("/config")
    def put_config(body: ConfigDocument):
        from senserve.gateway.errors import openai_error_response

        st = supervisor.status()
        if st.state == EngineState.SWITCHING:
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "message": "Cannot save config while model switch is in progress",
                        "type": "config_conflict",
                    }
                },
            )

        old_reg = supervisor.registry
        new_reg = preview_registry(body)
        active_id = supervisor.active_model_id()
        if st.state == EngineState.READY and config_affects_active_model(
            old_reg, new_reg, active_id
        ):
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "message": (
                            "Config change affects the active model; load another model "
                            "or wait until idle before editing source, vllm, or enabled flag"
                        ),
                        "type": "config_conflict",
                    }
                },
            )

        try:
            save_config_document(body)
            supervisor.reload_registry()
        except (ValueError, OSError) as exc:
            return openai_error_response(str(exc), status_code=400)
        except SwitchingError as exc:
            return JSONResponse(
                status_code=409,
                content={"error": {"message": str(exc), "type": "config_conflict"}},
            )

        return {"status": "saved", "path": str(models_path())}

    @router.get("/vllm/flags")
    def get_vllm_flags(refresh: bool = Query(False)):
        try:
            flags = vllm_flags.list_vllm_flags(refresh=refresh)
        except RuntimeError as exc:
            return JSONResponse(
                status_code=503,
                content={"error": {"message": str(exc), "type": "vllm_unavailable"}},
            )
        return {"flags": flags}

    @router.post("/models/load", status_code=202)
    def models_load(body: LoadModelRequest):
        try:
            supervisor.registry.get(body.model_id)
            supervisor.load(body.model_id)
        except SwitchingError as exc:
            from senserve.gateway.errors import switching_response

            return switching_response(str(exc))
        except KeyError:
            from senserve.gateway.errors import openai_error_response

            return openai_error_response(f"Unknown model: {body.model_id}", status_code=404)
        except ValueError as exc:
            from senserve.gateway.errors import openai_error_response

            return openai_error_response(str(exc), status_code=400)
        return {"status": "accepted", "model_id": body.model_id}

    return router
