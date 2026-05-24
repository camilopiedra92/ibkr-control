from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str
    jwt_secret: str
    jwt_lifetime_seconds: int = 3600
    backend_cors_origins: str = ""

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


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
