"""Fail-closed startup guard: the app must connect with a role that is SUBJECT
to RLS. A superuser or a role with rolbypassrls ignores every org_isolation
policy — booting under such a role silently disables tenant isolation. We
assert at startup and refuse to serve otherwise.
"""

import os
from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def assert_runtime_role_enforces_rls(engine: AsyncEngine) -> None:
    """Raise RuntimeError unless the connecting role is subject to RLS.

    Checks both vectors that bypass RLS in Postgres:
      * superuser  — bypasses RLS unconditionally.
      * rolbypassrls — the per-role BYPASSRLS attribute.
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT current_user AS role, "
                    "current_setting('is_superuser')::bool AS is_su, "
                    "COALESCE((SELECT rolbypassrls FROM pg_roles "
                    "WHERE rolname = current_user), false) AS bypass"
                )
            )
        ).one()
    if row.is_su or row.bypass:
        raise RuntimeError(
            f"Refusing to start: DB role '{row.role}' can bypass RLS "
            f"(is_superuser={row.is_su}, rolbypassrls={row.bypass}). The app must "
            f"connect as the non-bypass app_rls role so tenant isolation is "
            f"enforced. Point DATABASE_URL at app_rls (see compose: the backend "
            f"service uses app_rls; migrations run in the separate migrate service)."
        )


def assert_single_process(env: Mapping[str, str] | None = None) -> None:
    """Fail-loud si se configuró >1 worker (HD-7 / DEF-B).

    El scheduler (APScheduler in-process, sin leader election), JobTracker y
    Step3Stash son singletons per-proceso. Con >1 worker/réplica: los crons
    disparan una vez por worker, el SSE cae en el worker equivocado (404), y el
    Step3Stash del wizard es invisible entre workers (onboarding roto) — todo en
    SILENCIO. Hasta que SP5 extraiga el scheduler + cola durable, >1 worker está
    roto: fallar al arranque es correcto.

    LÍMITE DE COBERTURA (importante): este guard solo detecta el conteo de
    workers declarado por env-var (WEB_CONCURRENCY / UVICORN_WORKERS /
    GUNICORN_WORKERS — el lever idiomático de uvicorn/gunicorn). NO detecta
    (a) un `uvicorn ... --workers N` pasado directo por CLI/compose sin env-var,
    ni (b) N réplicas del contenedor (cada una un proceso single-worker que pasa
    el guard). Detectar réplicas desde adentro del proceso es imposible por
    diseño (eso ES leader election = SP5). O sea: HD-7 es un backstop PARCIAL
    del invariante 1-proceso, no total — la garantía completa llega con SP5.
    """
    env = os.environ if env is None else env
    for var in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"):
        raw = (env.get(var) or "").strip()
        if raw.isdigit() and int(raw) > 1:
            raise RuntimeError(
                f"{var}={raw}: correr >1 worker rompe crons/SSE/onboarding en SILENCIO "
                "(scheduler/JobTracker/Step3Stash son in-process, invariante 1-proceso). "
                "Ver DEF-B del spec pre-SP3 + SP5 (extracción del scheduler + cola durable) "
                "antes de escalar horizontalmente."
            )
