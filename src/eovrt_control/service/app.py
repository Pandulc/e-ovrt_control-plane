"""Factory de la app FastAPI del servicio control-plane (ADR-008)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from eovrt_control.service.routers import config as config_router
from eovrt_control.service.routers import health, runs
from eovrt_control.service.run_manager import RunManager
from eovrt_control.service.settings import ServiceSettings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings: ServiceSettings = app.state.settings
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    app.state.manager = RunManager(settings)
    logger.info("control-plane listo (runs_dir=%s)", settings.runs_dir)
    yield
    # SIGTERM/shutdown: cerrar el bus desbloquea el hilo de una corrida live.
    app.state.manager.shutdown()
    app.state.manager.join_active(timeout=10.0)


def create_app(settings: ServiceSettings | None = None) -> FastAPI:
    settings = settings or ServiceSettings.from_env()
    app = FastAPI(title="eovrt-control-plane", lifespan=_lifespan)
    app.state.settings = settings
    app.include_router(health.router)
    app.include_router(runs.router)
    app.include_router(config_router.router)
    return app
