from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ibkr_control.config import get_settings
from ibkr_control.api.connections import router as connections_router
from ibkr_control.api.grants import router as grants_router
from ibkr_control.api.health import router as health_router
from ibkr_control.api.imports import router as imports_router
from ibkr_control.api.ingest import router as ingest_router
from ibkr_control.api.setup import router as setup_router
from ibkr_control.auth.router import router as auth_router
from ibkr_control.settings.router import router as settings_router
from ibkr_control.scheduler import create_scheduler
from ibkr_control.scheduler.jobs import register_jobs


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: assert single-process (HD-7) and that the runtime DB role
    enforces RLS (fail-closed), then start APScheduler with daily ingest jobs.
    Shutdown: stop it.
    """
    from ibkr_control.db.guards import assert_runtime_role_enforces_rls, assert_single_process
    from ibkr_control.db.session import get_engine

    assert_single_process()
    await assert_runtime_role_enforces_rls(get_engine())

    scheduler = create_scheduler()
    register_jobs(scheduler)
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="IBKR Control Center", version="0.1.0", lifespan=lifespan)

    if settings.cors_origins_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_router, prefix="/api")
    app.include_router(settings_router, prefix="/api")
    app.include_router(connections_router, prefix="/api")
    app.include_router(health_router, prefix="/api")
    app.include_router(setup_router, prefix="/api")
    app.include_router(imports_router, prefix="/api")
    app.include_router(ingest_router, prefix="/api")
    app.include_router(grants_router, prefix="/api")
    return app


app = create_app()
