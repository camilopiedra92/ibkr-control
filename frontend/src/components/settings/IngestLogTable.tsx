"use client";

import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import {
  listLogsApiIngestLogsGet,
  IngestLogRead,
} from "@/lib/api";

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("es-CO", {
      dateStyle: "short",
      timeStyle: "short",
    });
  } catch {
    return iso;
  }
}

function statusBadge(status: string): string {
  switch (status) {
    case "ok":
      return "text-green-600";
    case "running":
      return "text-blue-600";
    case "failed":
      return "text-red-600";
    default:
      return "text-muted-foreground";
  }
}

interface IngestLogTableProps {
  /** If provided, the table will re-fetch when this value changes */
  refreshKey?: number;
}

export function IngestLogTable({ refreshKey }: IngestLogTableProps) {
  const [logs, setLogs] = useState<IngestLogRead[]>([]);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const { mutate: fetchLogs, isPending } = useMutation({
    mutationFn: () => listLogsApiIngestLogsGet({ limit: 10 }),
    onSuccess: (data) => {
      setLogs(data);
      setFetchError(null);
    },
    onError: () => {
      setFetchError("No se pudo cargar el historial de ingest");
    },
  });

  // Fetch on mount and whenever refreshKey changes
  useEffect(() => {
    fetchLogs();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Historial de ingest</h2>
        <Button
          variant="outline"
          size="sm"
          onClick={() => fetchLogs()}
          disabled={isPending}
        >
          {isPending ? "Cargando…" : "Actualizar"}
        </Button>
      </div>

      {fetchError && <p className="text-sm text-red-600">{fetchError}</p>}

      {!isPending && logs.length === 0 && !fetchError && (
        <p className="text-sm text-muted-foreground">
          Sin registros todavia. El primer ingest aparecera aqui.
        </p>
      )}

      {logs.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th className="pb-2 pr-4 font-medium">Iniciado</th>
                <th className="pb-2 pr-4 font-medium">Tipo</th>
                <th className="pb-2 pr-4 font-medium">Trigger</th>
                <th className="pb-2 pr-4 font-medium">Estado</th>
                <th className="pb-2 pr-4 font-medium text-right">Items</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((log) => (
                <tr key={log.id} className="border-b last:border-0">
                  <td className="py-2 pr-4 font-mono text-xs">
                    {formatDate(log.started_at)}
                  </td>
                  <td className="py-2 pr-4">{log.job_kind}</td>
                  <td className="py-2 pr-4 text-muted-foreground">
                    {log.trigger}
                  </td>
                  <td className={`py-2 pr-4 font-medium ${statusBadge(log.status)}`}>
                    {log.status}
                    {log.error_message && (
                      <span
                        className="ml-1 text-xs text-muted-foreground"
                        title={log.error_message}
                      >
                        (!)
                      </span>
                    )}
                  </td>
                  <td className="py-2 text-right tabular-nums">
                    {log.items_processed ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
