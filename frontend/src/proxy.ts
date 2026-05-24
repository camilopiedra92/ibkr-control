import { NextRequest, NextResponse } from "next/server";

/**
 * Middleware de routing.
 *
 * Nota de arquitectura: la autenticación en esta app usa JWT almacenado en
 * localStorage (no httpOnly cookie), por lo que el middleware no puede leer
 * el token ni consultar /api/setup/state del lado del servidor. La lógica de
 * redirección auth→/login y setup-incompleto→/setup la manejan los
 * componentes cliente AuthGate y SetupGate respectivamente.
 *
 * Lo que sí hace este middleware:
 * - Deja pasar activos estáticos y rutas de Next.js internamente.
 * - Expone el matcher para que sólo se ejecute en rutas relevantes.
 */
export function proxy(_req: NextRequest) {
  return NextResponse.next();
}

export const config = {
  matcher: [
    /*
     * Aplica a todas las rutas excepto:
     * - /_next/ (activos internos Next.js)
     * - /static/ (activos estáticos)
     * - archivos con extensión (favicon.ico, etc.)
     * - /api/ (rutas de API del backend expuestas por proxy)
     */
    "/((?!_next/|static/|api/|favicon\\.ico).*)",
  ],
};
