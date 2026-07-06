"""Route-sweep guard (SP2-D2): TODA ruta declara require_scope o esta en el
allowlist nombrado. Agregar un endpoint sin autorizacion rompe la suite —
misma clase de guard estructural que el boot guard RLS (PR #7).
"""

import inspect

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


def _matches_prefix(path: str, prefix: str) -> bool:
    """Match con boundary de segmento: /api/auth cubre /api/auth y /api/auth/...
    pero NO /api/authz-anything (startswith pelado = bypass latente del guard).
    El brazo == es necesario: /api/settings y /api/users existen como path exacto.
    """
    return path == prefix or path.startswith(prefix + "/")


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


def _route_scope(route: APIRoute) -> str | None:
    """Devuelve el string de scope declarado por la ruta (el primer ._authz_scope
    hallado en su árbol de dependencias), o None si no lleva require_scope.
    """
    found: list[str] = []

    def walk(dependencies) -> None:
        for dep in dependencies:
            scope = getattr(dep.call, "_authz_scope", None)
            if scope is not None:
                found.append(scope)
            walk(dep.dependencies)

    walk(route.dependant.dependencies)
    return found[0] if found else None


def test_every_route_declares_scope_or_is_allowlisted():
    app = create_app()
    unprotected = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            # NOTE: solo se barren APIRoute — WS/Mount/sub-apps quedan fuera
            # del guard; revisar si se agregan. (docs/openapi caen aca tambien)
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            if (method, route.path) in ALLOWLIST:
                continue
            if any(_matches_prefix(route.path, p) for p in ALLOWLISTED_PREFIXES):
                continue
            if not _has_scope(route):
                unprotected.append((method, route.path))
    assert not unprotected, (
        f"Rutas sin require_scope ni allowlist (agrega el scope o una entrada "
        f"NOMBRADA al allowlist): {unprotected}"
    )


def test_allowlist_has_no_stale_entries():
    """Una entrada del allowlist cuya ruta ya no existe es ruido — falla.

    Idem para los prefijos: un prefijo muerto/typo'd seria una exencion
    silenciosa acumulandose — cada prefijo debe matchear >=1 ruta viva
    (con el MISMO matcher de boundary de segmento que usa el sweep).
    """
    app = create_app()
    actual = {
        (m, r.path)
        for r in app.routes
        if isinstance(r, APIRoute)
        for m in r.methods - {"HEAD", "OPTIONS"}
    }
    stale = [k for k in ALLOWLIST if k not in actual]
    assert not stale, f"Entradas del allowlist sin ruta viva: {stale}"

    live_paths = {path for _, path in actual}
    dead_prefixes = [
        p for p in ALLOWLISTED_PREFIXES if not any(_matches_prefix(path, p) for path in live_paths)
    ]
    assert not dead_prefixes, f"Prefijos del allowlist sin ninguna ruta viva: {dead_prefixes}"


def test_data_read_routes_apply_party_scope():
    """HD-2: TODA ruta data:read debe aplicar la barrera-3 (party filter via
    visible_account_ids). Hoy solo list_restatements es data:read y ya la aplica;
    el guard lockea la convención ANTES de que Phase 3 agregue endpoints data-plane
    (un grantee de firma que salte el filtro ve las filas de TODOS los clientes del
    org, no solo los parties otorgados). Non-vacuo: exige >=1 ruta data:read viva.
    """
    app = create_app()
    data_read = [
        r for r in app.routes if isinstance(r, APIRoute) and _route_scope(r) == "data:read"
    ]
    assert data_read, (
        "No hay ninguna ruta data:read — el guard sería vacuo. ¿Se renombró el scope "
        "o se movió require_scope('data:read')?"
    )
    missing = [
        (method, r.path)
        for r in data_read
        for method in r.methods - {"HEAD", "OPTIONS"}
        if "visible_account_ids" not in inspect.getsource(r.endpoint)
    ]
    assert not missing, (
        "Rutas data:read que NO referencian visible_account_ids (barrera-3, SP2 — "
        f"todo endpoint data-plane DEBE filtrar por party): {missing}"
    )
