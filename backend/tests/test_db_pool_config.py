"""D2 (sp1-db-hardening): pool de conexiones explícito vía Settings.

Lockea que el presupuesto de conexiones sea una decisión declarada en config,
no defaults implícitos de SQLAlchemy dispersos en 3 call sites.
"""

from ibkr_control.config import get_settings
from ibkr_control.db.session import engine_kwargs


def test_pool_settings_defaults():
    s = get_settings()
    assert s.db_pool_size == 10
    assert s.db_max_overflow == 20
    assert s.db_pool_recycle == 1800


def test_engine_kwargs_reflects_settings_and_forces_pre_ping():
    kw = engine_kwargs()
    assert kw == {
        "pool_size": 10,
        "max_overflow": 20,
        "pool_recycle": 1800,
        "pool_pre_ping": True,
    }


def test_pool_settings_env_override(monkeypatch):
    monkeypatch.setenv("DB_POOL_SIZE", "3")
    get_settings.cache_clear()
    try:
        assert get_settings().db_pool_size == 3
        assert engine_kwargs()["pool_size"] == 3
    finally:
        get_settings.cache_clear()
