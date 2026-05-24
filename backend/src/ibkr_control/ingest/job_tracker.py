"""Singleton in-memory para tracking de eventos de jobs (alimenta SSE).

V1: in-process, single backend replica. V2: Redis pub/sub.
"""
from dataclasses import dataclass, field
from itertools import count
from typing import Any


@dataclass
class TrackerEvent:
    id: int
    payload: dict[str, Any]


@dataclass
class _JobState:
    events: list[TrackerEvent] = field(default_factory=list)
    done: bool = False
    next_event_id: int = 0


class JobTracker:
    """Singleton para emitir y consumir eventos por job_id."""

    def __init__(self) -> None:
        self._jobs: dict[int, _JobState] = {}
        self._next_job_id = count(start=1)

    def create_job(self) -> int:
        job_id = next(self._next_job_id)
        self._jobs[job_id] = _JobState()
        return job_id

    def emit(self, job_id: int, payload: dict[str, Any]) -> None:
        if job_id not in self._jobs:
            return
        state = self._jobs[job_id]
        event = TrackerEvent(id=state.next_event_id, payload=payload)
        state.events.append(event)
        state.next_event_id += 1

    def events_since(self, job_id: int, after_id: int) -> list[TrackerEvent]:
        if job_id not in self._jobs:
            return []
        return [e for e in self._jobs[job_id].events if e.id >= after_id]

    def mark_done(self, job_id: int) -> None:
        if job_id in self._jobs:
            self._jobs[job_id].done = True

    def is_done(self, job_id: int) -> bool:
        return job_id in self._jobs and self._jobs[job_id].done

    def cleanup(self, job_id: int) -> None:
        """Remueve el job del tracker (llamar despues de N minutos de done)."""
        self._jobs.pop(job_id, None)


# Singleton global usado por endpoints + jobs
_tracker_instance = JobTracker()


def get_tracker() -> JobTracker:
    return _tracker_instance
