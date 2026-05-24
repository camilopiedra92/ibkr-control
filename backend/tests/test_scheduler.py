"""Tests de registro de cron jobs."""
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ibkr_control.scheduler.jobs import register_jobs


def test_register_creates_three_jobs():
    scheduler = AsyncIOScheduler()
    register_jobs(scheduler)
    ids = {j.id for j in scheduler.get_jobs()}
    assert ids == {"flex_daily", "trm_daily", "cleanup_job_tracker"}


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
