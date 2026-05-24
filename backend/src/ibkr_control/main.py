from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ibkr_control.config import get_settings
from ibkr_control.auth.router import router as auth_router


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="IBKR Control Center", version="0.1.0")

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
    return app


app = create_app()
