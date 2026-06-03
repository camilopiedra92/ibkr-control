"""Tests del singleton in-memory job_tracker para SSE."""

from ibkr_control.ingest.job_tracker import JobTracker


def test_create_job_records_owner():
    """D1: cada job guarda el user_id de quien lo dispara, para que el SSE
    endpoint pueda rechazar accesos cruzados."""
    tracker = JobTracker()
    job_id = tracker.create_job(user_id=42)
    assert tracker.owner_id(job_id) == 42


def test_owner_id_unknown_job_returns_none():
    tracker = JobTracker()
    assert tracker.owner_id(99999) is None


def test_emit_and_read_events():
    tracker = JobTracker()
    job_id = tracker.create_job(user_id=1)
    tracker.emit(job_id, {"step": "trm_backfill", "status": "running"})
    tracker.emit(job_id, {"step": "trm_backfill", "status": "ok", "n_days": 12000})

    events = tracker.events_since(job_id, after_id=-1)  # all events from the start
    assert len(events) == 2
    assert events[0].payload == {"step": "trm_backfill", "status": "running"}
    assert events[1].payload["n_days"] == 12000


def test_events_since_filters_by_id():
    tracker = JobTracker()
    job_id = tracker.create_job(user_id=1)
    tracker.emit(job_id, {"step": "a"})  # id=0
    tracker.emit(job_id, {"step": "b"})  # id=1
    tracker.emit(job_id, {"step": "c"})  # id=2

    # after_id=1 → strictly greater than 1 → only event with id=2 ('c')
    events = tracker.events_since(job_id, after_id=1)
    assert len(events) == 1
    assert events[0].payload == {"step": "c"}

    # after_id=-1 → all events
    all_events = tracker.events_since(job_id, after_id=-1)
    assert len(all_events) == 3


def test_is_done_default_false():
    tracker = JobTracker()
    job_id = tracker.create_job(user_id=1)
    assert tracker.is_done(job_id) is False


def test_mark_done():
    tracker = JobTracker()
    job_id = tracker.create_job(user_id=1)
    tracker.mark_done(job_id)
    assert tracker.is_done(job_id) is True


def test_unknown_job_returns_empty():
    tracker = JobTracker()
    assert tracker.events_since(99999, after_id=0) == []
    assert tracker.is_done(99999) is False


def test_cleanup_old_removes_done_jobs_past_cutoff():
    """cleanup_old removes jobs that are done and older than the cutoff."""
    from datetime import datetime, timedelta, timezone

    tracker = JobTracker()
    job_id = tracker.create_job(user_id=1)
    tracker.emit(job_id, {"step": "done"})
    tracker.mark_done(job_id)

    # Backdate created_at to 2 hours ago to ensure it exceeds the 1-hour cutoff
    tracker._jobs[job_id].created_at = datetime.now(timezone.utc) - timedelta(hours=2)

    removed = tracker.cleanup_old(older_than=timedelta(hours=1))
    assert removed == 1
    assert not tracker.has_job(job_id)


def test_cleanup_old_keeps_jobs_not_yet_done():
    """cleanup_old does NOT remove in-progress jobs even if old."""
    from datetime import datetime, timedelta, timezone

    tracker = JobTracker()
    job_id = tracker.create_job(user_id=1)
    tracker.emit(job_id, {"step": "running"})
    # Backdate created_at to 2 hours ago
    tracker._jobs[job_id].created_at = datetime.now(timezone.utc) - timedelta(hours=2)

    removed = tracker.cleanup_old(older_than=timedelta(hours=1))
    assert removed == 0
    assert tracker.has_job(job_id)


def test_cleanup_old_keeps_recent_done_jobs():
    """cleanup_old does NOT remove done jobs younger than the cutoff."""
    from datetime import timedelta

    tracker = JobTracker()
    job_id = tracker.create_job(user_id=1)
    tracker.mark_done(job_id)
    # created_at is just now (default), well within the 1-hour cutoff

    removed = tracker.cleanup_old(older_than=timedelta(hours=1))
    assert removed == 0
    assert tracker.has_job(job_id)
