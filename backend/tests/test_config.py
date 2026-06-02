import pytest
from pydantic import ValidationError

from ibkr_control.config import Settings, get_settings


def test_settings_rejects_cors_wildcard(monkeypatch):
    # CORSMiddleware con allow_origins=["*"] + allow_credentials=True es spec-
    # invalid (browsers fallan silenciosamente). Fallar al boot en vez de en la
    # primera request.
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")
    monkeypatch.setenv("BACKEND_CORS_ORIGINS", "*")
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_settings_rejects_cors_wildcard_within_csv(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")
    monkeypatch.setenv("BACKEND_CORS_ORIGINS", "http://localhost:3000,*")
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_settings_accepts_concrete_origins(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")
    monkeypatch.setenv("BACKEND_CORS_ORIGINS", "http://localhost:3000,https://app.example.com")
    get_settings.cache_clear()
    s = Settings()  # type: ignore[call-arg]
    assert s.cors_origins_list == ["http://localhost:3000", "https://app.example.com"]


def test_settings_accepts_empty_cors(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")
    monkeypatch.setenv("BACKEND_CORS_ORIGINS", "")
    get_settings.cache_clear()
    s = Settings()  # type: ignore[call-arg]
    assert s.cors_origins_list == []


def test_database_url_sync_swaps_asyncpg_for_psycopg(monkeypatch):
    """Sync URL replaces +asyncpg with +psycopg for APScheduler jobstore use."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@host:5432/db")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")

    from ibkr_control.config import Settings, get_settings

    get_settings.cache_clear()

    s = Settings()  # type: ignore[call-arg]
    assert s.database_url_sync == "postgresql+psycopg://u:p@host:5432/db"


def test_database_url_sync_idempotent_if_already_sync(monkeypatch):
    """If DATABASE_URL is already a sync form, leave it alone."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@host:5432/db")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")

    from ibkr_control.config import Settings, get_settings

    get_settings.cache_clear()

    s = Settings()  # type: ignore[call-arg]
    assert s.database_url_sync == "postgresql+psycopg://u:p@host:5432/db"
