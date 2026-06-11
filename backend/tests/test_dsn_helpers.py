from tests.conftest_ephemeral_db import swap_dsn_database


def test_swap_dsn_database_replaces_last_path_segment():
    dsn = "postgresql+asyncpg://test:test@localhost:5432/test"
    assert (
        swap_dsn_database(dsn, "template_migrated")
        == "postgresql+asyncpg://test:test@localhost:5432/template_migrated"
    )


def test_swap_dsn_database_preserves_credentials_and_host():
    dsn = "postgresql+psycopg2://app_rls:pw@db.internal:6543/old_db"
    assert (
        swap_dsn_database(dsn, "postgres")
        == "postgresql+psycopg2://app_rls:pw@db.internal:6543/postgres"
    )
