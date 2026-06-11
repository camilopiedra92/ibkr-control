"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import {
  getIngestHealthApiHealthIngestGet,
  listRestatementsApiIngestRestatementsGet,
  type RestatementRead,
  type RestatementReadKind,
} from "@/lib/api";

// Same key the rest of Settings (and the dashboard banner) uses — the panel
// reuses the already-cached health query for its badge counts instead of
// opening a second health request.
const INGEST_HEALTH_QUERY_KEY = ["ingest-health"] as const;
const PAGE_LIMIT = 50;

/**
 * Exhaustive map from the 2-value kind union (a Pydantic Literal -> TS union via
 * orval) to its badge label. The switch has no string fallthrough: if the
 * backend adds a kind, TypeScript flags the missing case at compile time.
 */
function kindLabel(kind: RestatementReadKind): string {
  switch (kind) {
    case "value_update":
      return "Valor actualizado";
    case "sibling_row":
      return "Fila hermana";
    default: {
      const _exhaustive: never = kind;
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

function DetailTable() {
  const [offset, setOffset] = useState(0);
  const [sealedOnly, setSealedOnly] = useState(false);

  const { data, isLoading, isError, error } = useQuery({
    // Orval generated this GET as a mutation too; per the repo convention we use
    // TanStack useQuery directly with the generated fetch fn (no `as unknown`).
    queryKey: ["restatements", offset, sealedOnly] as const,
    queryFn: () =>
      listRestatementsApiIngestRestatementsGet({
        limit: PAGE_LIMIT,
        offset,
        sealed_only: sealedOnly,
      }),
    placeholderData: (prev) => prev,
  });

  const rows: RestatementRead[] = data ?? [];
  // Bare-list endpoint (no total): next/prev only — disable next when this page
  // returned fewer than limit rows (nothing left to page into).
  const canNext = rows.length === PAGE_LIMIT;
  const canPrev = offset > 0;

  function toggleSealed(checked: boolean) {
    setSealedOnly(checked);
    setOffset(0); // a filter change resets to the first page
  }

  return (
    <div className="space-y-3">
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={sealedOnly}
          onChange={(e) => toggleSealed(e.target.checked)}
        />
        Solo año sealed
      </label>

      {isLoading && (
        <p className="text-sm text-muted-foreground">Cargando…</p>
      )}

      {isError && (
        <p className="text-sm text-red-600">
          {backendDetail(error, "No se pudo cargar el detalle de restatements.")}
        </p>
      )}

      {!isLoading && !isError && rows.length === 0 && (
        <p className="text-sm text-muted-foreground">
          Sin restatements en los últimos registros.
        </p>
      )}

      {!isError && rows.length > 0 && (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b">
                  <th className="text-left py-2 pr-4">Tabla</th>
                  <th className="text-left py-2 pr-4">Columna</th>
                  <th className="text-left py-2 pr-4">Antes → Después</th>
                  <th className="text-left py-2 pr-4">Tipo</th>
                  <th className="text-left py-2 pr-4">Sealed</th>
                  <th className="text-left py-2">Fecha</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="border-b align-top">
                    <td className="py-2 pr-4 font-mono">{r.table_name}</td>
                    <td className="py-2 pr-4 font-mono">{r.column_name}</td>
                    <td className="py-2 pr-4">
                      <span className="font-mono">{r.old_value ?? "—"}</span>
                      <span className="text-muted-foreground"> → </span>
                      <span className="font-mono">{r.new_value ?? "—"}</span>
                    </td>
                    <td className="py-2 pr-4">
                      <span className="rounded-full px-2 py-0.5 text-xs font-medium bg-gray-100 text-gray-700">
                        {kindLabel(r.kind)}
                      </span>
                    </td>
                    <td className="py-2 pr-4">
                      {r.sealed_year ? (
                        <span className="rounded-full px-2 py-0.5 text-xs font-medium bg-red-100 text-red-800">
                          Año sealed
                        </span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                    <td className="py-2">{formatDate(r.detected_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={!canPrev}
              onClick={() => setOffset((o) => Math.max(0, o - PAGE_LIMIT))}
            >
              Anterior
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!canNext}
              onClick={() => setOffset((o) => o + PAGE_LIMIT)}
            >
              Siguiente
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

export function RestatementsPanel() {
  const [expanded, setExpanded] = useState(false);

  const { data: health } = useQuery({
    queryKey: INGEST_HEALTH_QUERY_KEY,
    queryFn: () => getIngestHealthApiHealthIngestGet(),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: true,
  });

  const recent = health?.restatements.recent_count ?? 0;
  const sealed = health?.restatements.sealed_count ?? 0;

  // Hidden entirely when there's nothing to report.
  if (recent === 0) return null;

  // Ámbar cuando hay restatements pero ninguno en año sellado; ROJO cuando al
  // menos uno cae en una declaración sellada ("tu declaración pudo cambiar").
  const badgeClass =
    sealed > 0
      ? "bg-red-100 text-red-800"
      : "bg-amber-100 text-amber-800";

  return (
    <section id="restatements" className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-lg font-medium">Restatements</h2>
        <span
          data-testid="restatements-badge"
          className={`rounded-full px-2 py-0.5 text-xs font-medium ${badgeClass}`}
        >
          {recent} restatements ({sealed} en año sealed) · últimos 7 días
        </span>
      </div>
      <p className="text-sm text-muted-foreground">
        IBKR cambió un valor material de un hecho ya importado, o emitió una fila
        hermana con distinto P&amp;L. Detección, nunca borra datos.
      </p>

      <Button
        variant="outline"
        size="sm"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? "Ocultar detalle" : "Ver detalle"}
      </Button>

      {expanded && <DetailTable />}
    </section>
  );
}
