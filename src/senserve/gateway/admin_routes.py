from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from senserve.engine import EngineSupervisor, SwitchingError


class LoadModelRequest(BaseModel):
    model_id: str


def create_admin_router(supervisor: EngineSupervisor) -> APIRouter:
    router = APIRouter(prefix="/v1/admin")

    @router.get("/models/status")
    def models_status():
        s = supervisor.status()
        return {
            "state": s.state.value,
            "active_model_id": s.active_model_id,
            "target_model_id": s.target_model_id,
            "message": s.message,
            "error": s.error,
        }

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
