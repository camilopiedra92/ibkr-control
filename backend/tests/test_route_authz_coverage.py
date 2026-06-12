"""Route-sweep guard (SP2-D2): TODA ruta declara require_scope o esta en el
allowlist nombrado. Agregar un endpoint sin autorizacion rompe la suite —
misma clase de guard estructural que el boot guard RLS (PR #7).
"""

from fastapi.routing import APIRoute

from ibkr_control.main import create_app

# (method, path) -> por que NO lleva require_scope. Cada entrada nombrada.
ALLOWLIST: dict[tuple[str, str], str] = {
    ("GET", "/health"): "liveness probe del container — sin auth por diseno",
    ("GET", "/api/ingest/stream/{job_id}"): "SSE user-scoped: ownership en JobTracker (D1)",
    # /api/auth/*, /api/users/* (fastapi-users) y /api/settings/* (user-scoped,
    # sin org) se allowlistean por PREFIJO abajo — son recursos de
    # identidad/usuario, no de org.
}
ALLOWLISTED_PREFIXES: dict[str, str] = {
    "/api/auth": "authN de fastapi-users — anterior a cualquier contexto org",
    "/api/users": "gestion de identidad de fastapi-users (self/superuser) — sin contexto org",
    "/api/settings": "UserSettings es user-scoped (sin organization_id)",
}


def _has_scope(route: APIRoute) -> bool:
    """True si la ruta (o cualquier sub-dependency en su arbol) lleva el marker
    ._authz_scope que require_scope adjunta a su closure. Recursivo porque
    FastAPI anida los Dependant.
    """

    def walk(dependencies) -> bool:
        for dep in dependencies:
            if getattr(dep.call, "_authz_scope", None) is not None:
                return True
            if walk(dep.dependencies):
                return True
        return False

    return walk(route.dependant.dependencies)


def test_every_route_declares_scope_or_is_allowlisted():
    app = create_app()
    unprotected = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue  # docs, openapi, etc. — no son APIRoute de la app
        for method in route.methods - {"HEAD", "OPTIONS"}:
            if (method, route.path) in ALLOWLIST:
                continue
            if any(route.path.startswith(p) for p in ALLOWLISTED_PREFIXES):
                continue
            if not _has_scope(route):
                unprotected.append((method, route.path))
    assert not unprotected, (
        f"Rutas sin require_scope ni allowlist (agrega el scope o una entrada "
        f"NOMBRADA al allowlist): {unprotected}"
    )


def test_allowlist_has_no_stale_entries():
    """Una entrada del allowlist cuya ruta ya no existe es ruido — falla."""
    app = create_app()
    actual = {
        (m, r.path)
        for r in app.routes
        if isinstance(r, APIRoute)
        for m in r.methods - {"HEAD", "OPTIONS"}
    }
    stale = [k for k in ALLOWLIST if k not in actual]
    assert not stale, f"Entradas del allowlist sin ruta viva: {stale}"
