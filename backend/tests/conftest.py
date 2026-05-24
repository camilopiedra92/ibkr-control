import os

# Defaults seteados ANTES de cualquier import de ibkr_control.*, porque pytest
# carga conftest.py antes de colectar test modules. Sin esto, test_health.py
# falla en collection time post-Task 5 cuando main.py importe auth → db.session
# → get_settings() → ValidationError por DATABASE_URL/JWT_SECRET faltantes.
# Tests que necesitan DB real (test_db_connection, test_auth) sobreescriben
# vía monkeypatch — setdefault no piso valores ya seteados.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")

import pytest


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"
