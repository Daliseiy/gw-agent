import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.middleware.request_logging import RequestLoggingMiddleware
from app.api.routers.auth import router as auth_router
from app.api.routers.health import router as health_router
from app.api.routers.ui import router as ui_router
from app.api.routers.voice import router as voice_router
from app.core.config import settings
from app.core.logging import configure_logging


configure_logging()
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if settings.log_http_requests:
        app.add_middleware(RequestLoggingMiddleware)

    if settings.static_dir.exists():
        app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")

    app.include_router(ui_router)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(voice_router)

    @app.on_event("startup")
    async def _on_startup() -> None:
        logger.info(
            "App startup | env=%s model=%s vertex_ai=%s project=%s location=%s",
            settings.environment,
            settings.gemini_model,
            settings.gemini_use_vertex_ai,
            settings.vertex_ai_project or "auto",
            settings.vertex_ai_location,
        )

    return app


app = create_app()
