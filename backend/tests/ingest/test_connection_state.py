"""Unit tests de la state machine de Connection (W4, T1-D5).

Mutadores puros sobre el objeto ORM (sin session) — el caller commitea.
"""

from datetime import datetime, timezone

from ibkr_control.db.models.connections import Connection
from ibkr_control.ingest import connection_state as cs

NOW = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)


def _conn(status: str = "active", failures: int = 0) -> Connection:
    return Connection(
        organization_id=1,
        institution_id=1,
        provider_type="ibkr_flex",
        status=status,
        consecutive_failures=failures,
    )


def test_sync_ok_resets_to_active():
    c = _conn(status="degraded", failures=3)
    cs.mark_sync_ok(c, now=NOW)
    assert c.status == "active"
    assert c.consecutive_failures == 0
    assert c.status_reason is None
    assert c.last_sync_at == NOW
    assert c.last_sync_status == "ok"


def test_sync_ok_after_reauth_required_recovers():
    # El usuario arregló el token por fuera — el retry natural lo detecta.
    c = _conn(status="reauth_required", failures=1)
    cs.mark_sync_ok(c, now=NOW)
    assert c.status == "active"


def test_auth_failure_goes_reauth_required():
    c = _conn()
    cs.mark_auth_failed(c, reason="Flex auth error 1018: bad token", now=NOW)
    assert c.status == "reauth_required"
    assert "1018" in c.status_reason
    assert c.last_sync_status == "failed"


def test_transient_failure_degrades_at_threshold():
    c = _conn()
    cs.mark_sync_failed(c, reason="boom", now=NOW)
    assert c.status == "active"  # 1 fallo: aún no degraded
    assert c.consecutive_failures == 1
    cs.mark_sync_failed(c, reason="boom", now=NOW)
    assert c.status == "degraded"  # 2do consecutivo: umbral R4
    assert c.consecutive_failures == 2


def test_transient_failure_does_not_mask_reauth_required():
    c = _conn(status="reauth_required", failures=2)
    cs.mark_sync_failed(c, reason="net", now=NOW)
    assert c.status == "reauth_required"  # estado más específico se conserva


def test_disabled_is_sticky_for_sync_events():
    c = _conn(status="disabled")
    cs.mark_sync_ok(c, now=NOW)
    assert c.status == "disabled"
    cs.mark_auth_failed(c, reason="x", now=NOW)
    assert c.status == "disabled"


def test_rotate_reactivates_unless_disabled():
    c = _conn(status="reauth_required", failures=4)
    cs.mark_rotated(c)
    assert c.status == "active"
    assert c.consecutive_failures == 0
    d = _conn(status="disabled")
    cs.mark_rotated(d)
    assert d.status == "disabled"  # rotar no des-pausa


def test_set_enabled_toggles():
    c = _conn(status="degraded", failures=5)
    cs.set_enabled(c, enabled=False)
    assert c.status == "disabled"
    cs.set_enabled(c, enabled=True)
    assert c.status == "active"
    assert c.consecutive_failures == 0
