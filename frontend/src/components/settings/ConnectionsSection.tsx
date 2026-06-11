"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  listConnectionsApiConnectionsGet,
  createConnectionApiConnectionsPost,
  disableConnectionApiConnectionsConnectionIdDisablePost,
  enableConnectionApiConnectionsConnectionIdEnablePost,
  deleteConnectionApiConnectionsConnectionIdDelete,
  type ConnectionRead,
  type ConnectionReadStatus,
} from "@/lib/api";
import { RotateTokenModal } from "./RotateTokenModal";

const CONNECTIONS_QUERY_KEY = ["connections"] as const;

interface BadgeSpec {
  label: string;
  className: string;
}

/**
 * Exhaustive map from the 4-value status union (a Pydantic Literal -> TS union
 * via orval) to its badge. The switch below has no string fallthrough: if the
 * backend adds a status, TypeScript flags the missing case at compile time.
 */
function statusBadge(status: ConnectionReadStatus): BadgeSpec {
  switch (status) {
    case "active":
      return { label: "Activa", className: "bg-green-100 text-green-800" };
    case "degraded":
      return { label: "Degradada", className: "bg-amber-100 text-amber-800" };
    case "reauth_required":
      return {
        label: "Requiere re-autenticación",
        className: "bg-red-100 text-red-800",
      };
    case "disabled":
      return { label: "Pausada", className: "bg-gray-100 text-gray-700" };
    default: {
      // Compile-time exhaustiveness guard: `never` errors if a case is missing.
      const _exhaustive: never = status;
      return _exhaustive;
    }
  }
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("es-CO", {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return iso;
  }
}

function backendDetail(err: unknown, fallback: string): string {
  const e = err as { response?: { data?: { detail?: unknown } } };
  const detail = e?.response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}

function ConnectionCard({
  conn,
  onRotate,
}: {
  conn: ConnectionRead;
  onRotate: (id: number) => void;
}) {
  const queryClient = useQueryClient();
  const [actionError, setActionError] = useState<string | null>(null);
  const badge = statusBadge(conn.status);

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: CONNECTIONS_QUERY_KEY });
  }

  const { mutate: disable, isPending: disabling } = useMutation({
    mutationFn: () => disableConnectionApiConnectionsConnectionIdDisablePost(conn.id),
    onSuccess: () => {
      setActionError(null);
      invalidate();
    },
    onError: (err) => setActionError(backendDetail(err, "No se pudo pausar la conexión")),
  });

  const { mutate: enable, isPending: enabling } = useMutation({
    mutationFn: () => enableConnectionApiConnectionsConnectionIdEnablePost(conn.id),
    onSuccess: () => {
      setActionError(null);
      invalidate();
    },
    onError: (err) => setActionError(backendDetail(err, "No se pudo reanudar la conexión")),
  });

  const { mutate: remove, isPending: removing } = useMutation({
    mutationFn: () => deleteConnectionApiConnectionsConnectionIdDelete(conn.id),
    onSuccess: () => {
      setActionError(null);
      invalidate();
    },
    onError: (err) => setActionError(backendDetail(err, "No se pudo eliminar la conexión")),
  });

  const isPaused = conn.status === "disabled";
  const busy = disabling || enabling || removing;

  return (
    <div className="rounded-xl ring-1 ring-foreground/10 p-4 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-base font-medium">
          {conn.display_name ?? "Conexión IBKR"}
        </h3>
        <span
          data-testid={`status-badge-${conn.id}`}
          className={`rounded-full px-2 py-0.5 text-xs font-medium ${badge.className}`}
        >
          {badge.label}
        </span>
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-muted-foreground">Query ID</dt>
        <dd className="font-mono">{conn.query_id || "—"}</dd>

        <dt className="text-muted-foreground">Última sincronización</dt>
        <dd>{formatDate(conn.last_sync_at)}</dd>
      </dl>

      {conn.status_reason && (
        <p
          className={`text-sm ${
            conn.status === "reauth_required" ? "text-red-600" : "text-amber-700"
          }`}
        >
          {conn.status_reason}
        </p>
      )}

      {actionError && <p className="text-sm text-red-600">{actionError}</p>}

      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => onRotate(conn.id)}
          disabled={busy}
        >
          Rotar token
        </Button>
        {isPaused ? (
          <Button variant="outline" size="sm" onClick={() => enable()} disabled={busy}>
            Reanudar
          </Button>
        ) : (
          <Button variant="outline" size="sm" onClick={() => disable()} disabled={busy}>
            Pausar
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          className="text-red-600 hover:text-red-700"
          onClick={() => {
            if (window.confirm("¿Eliminar esta conexión? Esta acción no se puede deshacer.")) {
              remove();
            }
          }}
          disabled={busy}
        >
          Eliminar
        </Button>
      </div>
    </div>
  );
}

function AddConnectionForm() {
  const queryClient = useQueryClient();
  const [token, setToken] = useState("");
  const [queryId, setQueryId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const { mutate, isPending } = useMutation({
    mutationFn: () =>
      createConnectionApiConnectionsPost({
        token: token.trim(),
        query_id: queryId.trim(),
        display_name: displayName.trim() || null,
      }),
    onSuccess: () => {
      setError(null);
      setToken("");
      setQueryId("");
      setDisplayName("");
      void queryClient.invalidateQueries({ queryKey: CONNECTIONS_QUERY_KEY });
    },
    onError: (err) =>
      // 401 token inválido / 400 query inválido / 502 No pude alcanzar IBKR:
      // el backend manda detail accionable, lo mostramos verbatim.
      setError(backendDetail(err, "No se pudo agregar la conexión")),
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    mutate();
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3 rounded-xl ring-1 ring-foreground/10 p-4">
      <h3 className="text-base font-medium">Agregar conexión IBKR</h3>
      <div className="space-y-2">
        <Label htmlFor="conn-token">Flex Token (read-only)</Label>
        <Input
          id="conn-token"
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="Token de 10+ caracteres"
          autoComplete="off"
          required
          minLength={10}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="conn-query-id">YTD Flex Query ID</Label>
        <Input
          id="conn-query-id"
          value={queryId}
          onChange={(e) => setQueryId(e.target.value)}
          placeholder="Ej: 123456"
          required
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="conn-display-name">Nombre de la conexión (opcional)</Label>
        <Input
          id="conn-display-name"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          placeholder="Ej: Cuenta personal"
        />
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <div className="flex justify-end">
        <Button type="submit" disabled={isPending}>
          {isPending ? "Validando…" : "Agregar conexión"}
        </Button>
      </div>
    </form>
  );
}

export function ConnectionsSection() {
  const [rotateId, setRotateId] = useState<number | null>(null);
  const queryClient = useQueryClient();

  const {
    data: connections,
    isLoading,
    isError,
  } = useQuery({
    queryKey: CONNECTIONS_QUERY_KEY,
    queryFn: () => listConnectionsApiConnectionsGet(),
  });

  function handleRotateSuccess() {
    setRotateId(null);
    void queryClient.invalidateQueries({ queryKey: CONNECTIONS_QUERY_KEY });
  }

  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">Conexiones IBKR</h2>
        <p className="text-sm text-muted-foreground">
          Cada conexión sincroniza una Flex Query de IBKR. Una conexión que
          requiere re-autenticación dejó de sincronizar hasta que rotés el token.
        </p>
      </div>

      {isLoading && <p className="text-sm text-muted-foreground">Cargando…</p>}

      {isError && (
        <p className="text-sm text-red-600">No se pudieron cargar las conexiones.</p>
      )}

      {connections && connections.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No hay conexiones configuradas. Agregá una para empezar a sincronizar.
        </p>
      )}

      {connections && connections.length > 0 && (
        <div className="space-y-3">
          {connections.map((conn) => (
            <ConnectionCard key={conn.id} conn={conn} onRotate={setRotateId} />
          ))}
        </div>
      )}

      <AddConnectionForm />

      {rotateId !== null && (
        <RotateTokenModal
          connectionId={rotateId}
          currentQueryId={
            connections?.find((c) => c.id === rotateId)?.query_id ?? ""
          }
          onSuccess={handleRotateSuccess}
          onCancel={() => setRotateId(null)}
        />
      )}
    </section>
  );
}
