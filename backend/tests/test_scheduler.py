"""Tests de registro de cron jobs."""

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ibkr_control.scheduler.jobs import register_jobs


def _persistence_noop() -> None:
    """Module-level noop so APScheduler SQLAlchemyJobStore can serialize the reference.

    APScheduler stores a string-path reference like
    'tests.test_scheduler:_persistence_noop' — anonymous lambdas would fail
    to serialize via the jobstore.
    """
    pass


def test_register_creates_three_jobs():
    scheduler = AsyncIOScheduler()
    register_jobs(scheduler)
    ids = {j.id for j in scheduler.get_jobs()}
    assert ids == {"flex_daily", "trm_daily", "cleanup_job_tracker"}


def test_flex_daily_references_per_org_runner():
    """register_jobs wires the per-ORG runner (not the old per-user one)."""
    from ibkr_control.scheduler import jobs as jobs_mod

    assert hasattr(jobs_mod, "_run_flex_for_all_orgs")
    assert not hasattr(jobs_mod, "_run_flex_for_all_users")

    scheduler = AsyncIOScheduler()
    register_jobs(scheduler)
    flex = next(j for j in scheduler.get_jobs() if j.id == "flex_daily")
    assert flex.func is jobs_mod._run_flex_for_all_orgs


@pytest.mark.asyncio
async def test_run_flex_for_all_orgs_iterates_distinct_orgs(
    db_session, sample_org, sample_user, monkeypatch
):
    """The Flex cron iterates distinct flex_credentials.organization_id and calls
    flex_job.run(organization_id=<org>, user_id=None, trigger='cron') for each —
    user_id=None because a cron org-batch has no triggering user (audit)."""
    from ibkr_control.db.models.flex_credentials import FlexCredentials
    from ibkr_control.db.models.organizations import Organization
    from ibkr_control.ingest.flex import job as flex_job
    from ibkr_control.scheduler import jobs as jobs_mod

    # Second tenant with its own creds.
    org2 = Organization(type="personal", name="Org Two")
    db_session.add(org2)
    await db_session.flush()

    db_session.add(
        FlexCredentials(organization_id=sample_org.id, token_encrypted=b"x", ytd_query_id="q1")
    )
    db_session.add(
        FlexCredentials(organization_id=org2.id, token_encrypted=b"y", ytd_query_id="q2")
    )
    await db_session.commit()

    calls: list[dict] = []

    async def fake_run(session_factory, *, organization_id, user_id, trigger):
        calls.append({"organization_id": organization_id, "user_id": user_id, "trigger": trigger})

    monkeypatch.setattr(flex_job, "run", fake_run)

    await jobs_mod._run_flex_for_all_orgs()

    called_orgs = {c["organization_id"] for c in calls}
    assert called_orgs == {sample_org.id, org2.id}
    assert all(c["user_id"] is None for c in calls)
    assert all(c["trigger"] == "cron" for c in calls)


def test_flex_daily_runs_at_12utc():
    """Flex cron a las 07:00 COT = 12:00 UTC (Bogota sin DST)."""
    scheduler = AsyncIOScheduler()
    register_jobs(scheduler)
    flex_job = next(j for j in scheduler.get_jobs() if j.id == "flex_daily")
    # APScheduler internal: fields[5] es hour, fields[6] es minute (en CronTrigger)
    hour_field = flex_job.trigger.fields[5]
    minute_field = flex_job.trigger.fields[6]
    assert hour_field.name == "hour"
    assert str(hour_field) == "12"
    assert str(minute_field) == "0"


def test_trm_daily_runs_at_0030utc():
    """TRM cron a las 19:30 COT del dia N = 00:30 UTC del dia N+1."""
    scheduler = AsyncIOScheduler()
    register_jobs(scheduler)
    trm_job = next(j for j in scheduler.get_jobs() if j.id == "trm_daily")
    hour_field = trm_job.trigger.fields[5]
    minute_field = trm_job.trigger.fields[6]
    assert str(hour_field) == "0"
    assert str(minute_field) == "30"


def test_jobs_have_max_instances_1_and_coalesce():
    scheduler = AsyncIOScheduler()
    register_jobs(scheduler)
    for j in scheduler.get_jobs():
        assert j.max_instances == 1
        assert j.coalesce is True


def test_create_scheduler_uses_sqlalchemy_jobstore(monkeypatch):
    """Factory returns AsyncIOScheduler configured with persistent jobstore."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-minimum-please-ok")

    from ibkr_control.config import get_settings

    get_settings.cache_clear()

    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    from ibkr_control.scheduler import create_scheduler

    scheduler = create_scheduler()
    jobstore = scheduler._jobstores["default"]  # noqa: SLF001 (internal access intentional)
    assert isinstance(jobstore, SQLAlchemyJobStore)


def test_jobs_persist_across_scheduler_instances(postgres_container):
    """SQLAlchemyJobStore actually persists jobs across separate scheduler instances.

    Simulates the "container restart" scenario: scheduler A adds a job, shuts
    down, scheduler B (new instance, same DB) recovers the job from the
    persistent jobstore.

    Uses BackgroundScheduler (not AsyncIO) to avoid event-loop complications
    in a sync test — the jobstore behavior is identical regardless of
    scheduler class.
    """
    from datetime import datetime, timedelta, timezone

    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    from apscheduler.schedulers.background import BackgroundScheduler

    async_url = postgres_container.get_connection_url()
    sync_url = async_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    jobstore_kwargs = dict(url=sync_url, tablename="apscheduler_jobs_test_persist")

    s1 = BackgroundScheduler(jobstores={"default": SQLAlchemyJobStore(**jobstore_kwargs)})
    s1.start(paused=True)
    try:
        s1.add_job(
            _persistence_noop,
            trigger="date",
            run_date=datetime.now(timezone.utc) + timedelta(days=365),
            id="persist_test",
            replace_existing=True,
        )
    finally:
        s1.shutdown(wait=False)

    # New scheduler, same DB → should recover the job
    s2 = BackgroundScheduler(jobstores={"default": SQLAlchemyJobStore(**jobstore_kwargs)})
    s2.start(paused=True)
    try:
        recovered = s2.get_job("persist_test")
        assert recovered is not None, "Job was not persisted by SQLAlchemyJobStore"
        assert recovered.id == "persist_test"
        s2.remove_job("persist_test")
    finally:
        s2.shutdown(wait=False)


def test_jobs_have_misfire_grace_time_set():
    """Jobs configured with 6h grace so a restart between trigger and exec catches up."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    scheduler = AsyncIOScheduler()
    register_jobs(scheduler)
    for j in scheduler.get_jobs():
        assert j.misfire_grace_time == 21600, f"{j.id} missing misfire_grace_time=21600"
