"""apscheduler_jobs is owner-created in a migration and app_rls can DML it,
so the scheduler (running as app_rls) never needs CREATE on schema public.
"""

from sqlalchemy import text


async def test_apscheduler_jobs_exists_and_app_rls_can_dml(app_rls_db_session):
    # Table exists post-migration (created by owner) ...
    exists = (
        await app_rls_db_session.scalars(text("SELECT to_regclass('public.apscheduler_jobs')"))
    ).one()
    assert exists == "apscheduler_jobs"

    # ... and app_rls has DML on it (insert a row, read it back, clean up).
    await app_rls_db_session.execute(
        text("INSERT INTO apscheduler_jobs (id, next_run_time, job_state) VALUES ('t1', 1.0, :s)"),
        {"s": b"x"},
    )
    n = (
        await app_rls_db_session.scalars(
            text("SELECT count(*) FROM apscheduler_jobs WHERE id = 't1'")
        )
    ).one()
    assert n == 1
    await app_rls_db_session.execute(text("DELETE FROM apscheduler_jobs WHERE id = 't1'"))
    await app_rls_db_session.commit()
