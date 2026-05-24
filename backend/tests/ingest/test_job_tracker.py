"""Tests del singleton in-memory job_tracker para SSE."""
import pytest
from ibkr_control.ingest.job_tracker import JobTracker


def test_emit_and_read_events():
    tracker = JobTracker()
    job_id = tracker.create_job()
    tracker.emit(job_id, {"step": "trm_backfill", "status": "running"})
    tracker.emit(job_id, {"step": "trm_backfill", "status": "ok", "n_days": 12000})

    events = tracker.events_since(job_id, after_id=0)
    assert len(events) == 2
    assert events[0].payload == {"step": "trm_backfill", "status": "running"}
    assert events[1].payload["n_days"] == 12000


def test_events_since_filters_by_id():
    tracker = JobTracker()
    job_id = tracker.create_job()
    tracker.emit(job_id, {"step": "a"})
    tracker.emit(job_id, {"step": "b"})
    tracker.emit(job_id, {"step": "c"})

    events = tracker.events_since(job_id, after_id=1)
    assert len(events) == 2
    assert events[0].payload == {"step": "b"}


def test_is_done_default_false():
    tracker = JobTracker()
    job_id = tracker.create_job()
    assert tracker.is_done(job_id) is False


def test_mark_done():
    tracker = JobTracker()
    job_id = tracker.create_job()
    tracker.mark_done(job_id)
    assert tracker.is_done(job_id) is True


def test_unknown_job_returns_empty():
    tracker = JobTracker()
    assert tracker.events_since(99999, after_id=0) == []
    assert tracker.is_done(99999) is False
