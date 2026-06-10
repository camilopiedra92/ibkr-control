from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str
    jwt_secret: str
    jwt_lifetime_seconds: int = 3600
    backend_cors_origins: str = ""

    # Upload limits
    max_xml_size_bytes: int = 50 * 1024 * 1024  # 50 MB

    # Rate limiting
    ingest_trigger_cooldown_seconds: int = 300  # 5 minutes

    # DB connection pool (D2 sp1-db-hardening). Presupuesto total de
    # conexiones por réplica = app (pool_size + max_overflow) + engines
    # efímeros de crons (lazy, se disponen al final de cada run) + jobstore
    # sync de APScheduler (psycopg, pool default chico). Tunables por env en
    # Coolify sin redeploy. pool_pre_ping NO es configurable: siempre True
    # (no hay caso legítimo para apagarlo).
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle: int = 1800

    @field_validator("backend_cors_origins")
    @classmethod
    def _no_cors_wildcard(cls, v: str) -> str:
        # CORSMiddleware con allow_origins=["*"] y allow_credentials=True es
        # spec-invalid: el browser rechaza la response silenciosamente. Fallar
        # al boot en vez de en runtime. Si alguna vez se quiere wildcard,
        # tambien hay que desactivar credentials — decision consciente, no
        # accidental por env var mal seteado.
        origins = [o.strip() for o in v.split(",") if o.strip()]
        if "*" in origins:
            raise ValueError(
                "BACKEND_CORS_ORIGINS='*' is incompatible with allow_credentials=True; "
                "list explicit origins instead"
            )
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]

    @property
    def database_url_sync(self) -> str:
        """Sync DB URL for APScheduler jobstore (uses psycopg v3, not asyncpg).

        APScheduler 3.x SQLAlchemyJobStore is sync-only; it issues blocking
        SELECT/UPDATE/INSERT against `apscheduler_jobs` from within
        AsyncIOScheduler's wake-up thread. We need a sync driver distinct
        from the async one used by the app's request pipeline.
        """
        return self.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
