from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from senserve import __version__
from senserve.engine import EngineState, EngineSupervisor
from senserve.gateway.admin_routes import create_admin_router
from senserve.gateway.middleware import MaxBodySizeMiddleware
from senserve.gateway.openai_routes import create_openai_router
from senserve.settings import get_settings

_UI_DIR = Path(__file__).resolve().parent / "static" / "ui"


def create_app(supervisor: EngineSupervisor | None = None) -> FastAPI:
    settings = get_settings()
    sup = supervisor or EngineSupervisor()

    app = FastAPI(title="Senserve", version=__version__)
    app.state.supervisor = sup

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=settings.max_upload_bytes)

    @app.get("/health")
    def health():
        st = sup.status()
        return {
            "status": "ok",
            "gateway": "ok",
            "engine": st.state.value,
            "active_model": st.active_model_id,
            "ready": st.state == EngineState.READY,
        }

    app.include_router(create_openai_router(sup))
    app.include_router(create_admin_router(sup))

    if _UI_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=_UI_DIR, html=True), name="ui")

    return app
