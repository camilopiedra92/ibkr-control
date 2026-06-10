"""Única puerta de transiciones del status de Connection (W4, spec T1-D5).

Mutadores puros sobre el objeto ORM — sin session, sin commit (el caller
persiste). Reglas:
- `disabled` es sticky para eventos de sync (solo set_enabled lo cambia).
- `reauth_required` no se enmascara con fallos transitorios posteriores
  (estado más específico gana); un sync OK sí lo limpia (token arreglado).
- 2 fallos transitorios consecutivos => degraded (umbral compartido con el
  health banner R4).
"""

from datetime import datetime

from ibkr_control.db.models.connections import Connection

DEGRADED_THRESHOLD = 2
_REASON_MAX = 500


def mark_sync_ok(conn: Connection, *, now: datetime) -> None:
    if conn.status != "disabled":
        conn.status = "active"
    conn.status_reason = None
    conn.consecutive_failures = 0
    conn.last_sync_at = now
    conn.last_sync_status = "ok"


def mark_auth_failed(conn: Connection, *, reason: str, now: datetime) -> None:
    if conn.status != "disabled":
        conn.status = "reauth_required"
    conn.status_reason = reason[:_REASON_MAX]
    conn.consecutive_failures += 1
    conn.last_sync_at = now
    conn.last_sync_status = "failed"


def mark_sync_failed(conn: Connection, *, reason: str, now: datetime) -> None:
    conn.consecutive_failures += 1
    conn.status_reason = reason[:_REASON_MAX]
    conn.last_sync_at = now
    conn.last_sync_status = "failed"
    if (
        conn.status not in ("disabled", "reauth_required")
        and conn.consecutive_failures >= DEGRADED_THRESHOLD
    ):
        conn.status = "degraded"


def mark_rotated(conn: Connection) -> None:
    conn.consecutive_failures = 0
    conn.status_reason = None
    if conn.status != "disabled":
        conn.status = "active"


def set_enabled(conn: Connection, *, enabled: bool) -> None:
    if enabled:
        conn.status = "active"
        conn.consecutive_failures = 0
        conn.status_reason = None
    else:
        conn.status = "disabled"
