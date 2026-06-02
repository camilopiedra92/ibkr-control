"""In-memory stash for Step 3 XML uploads, with TTL.

Per spec D11: parsed-but-not-committed XMLs live in process memory
while the user resolves new_accounts. Container restart loses stash
(user re-uploads). Mirror of JobTracker pattern; not DB-backed
because TTL is < container lifetime in normal operation.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StashEntry:
    temp_id: str
    user_id: int
    data: Any  # ParsedFlexData; typed Any to avoid circular import
    sha256: str
    expires_at: float = field(default=0.0)


class Step3Stash:
    """Per-process dict; safe for single-replica single-process app."""

    def __init__(self, ttl_seconds: float = 3600) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, StashEntry] = {}

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [tid for tid, e in self._entries.items() if e.expires_at < now]
        for tid in expired:
            self._entries.pop(tid, None)

    def put(self, *, user_id: int, data: Any, sha256: str) -> str:
        self._prune()
        temp_id = uuid.uuid4().hex
        self._entries[temp_id] = StashEntry(
            temp_id=temp_id,
            user_id=user_id,
            data=data,
            sha256=sha256,
            expires_at=time.monotonic() + self._ttl,
        )
        return temp_id

    def get(self, *, user_id: int, temp_id: str) -> StashEntry | None:
        self._prune()
        entry = self._entries.get(temp_id)
        if entry is None or entry.user_id != user_id:
            return None
        return entry

    def pop(self, *, user_id: int, temp_id: str) -> StashEntry | None:
        entry = self.get(user_id=user_id, temp_id=temp_id)
        if entry is None:
            return None
        self._entries.pop(temp_id, None)
        return entry

    def list_for_user(self, *, user_id: int) -> list[StashEntry]:
        self._prune()
        return [e for e in self._entries.values() if e.user_id == user_id]

    def find_by_sha256(self, *, user_id: int, sha256: str) -> StashEntry | None:
        self._prune()
        for e in self._entries.values():
            if e.user_id == user_id and e.sha256 == sha256:
                return e
        return None


_singleton: Step3Stash | None = None


def get_stash() -> Step3Stash:
    """Module-level singleton accessor (mirrors JobTracker pattern)."""
    global _singleton
    if _singleton is None:
        _singleton = Step3Stash(ttl_seconds=3600)
    return _singleton
