"use client";

import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { triggerManualRefreshApiIngestTriggerPost } from "@/lib/api";
import { useIngestStream, StreamEvent } from "@/hooks/useIngestStream";

const SUBSTEP_LABELS: Record<string, string> = {
  trm_backfill: "TRM (DIAN)",
  flex_ytd: "Flex YTD (IBKR)",
  done: "Completado",
};

function substepColor(status: string | undefined): string {
  switch (status) {
    case "running":
      return "text-blue-600";
    case "ok":
      return "text-green-600";
    case "failed":
      return "text-red-600";
    default:
      return "text-muted-foreground";
  }
}

function substepIcon(status: string | undefined): string {
  switch (status) {
    case "running":
      return "⏳";
    case "ok":
      return "✓";
    case "failed":
      return "✗";
    default:
      return "⏸";
  }
}

interface SubstepState {
  status: "pending" | "running" | "ok" | "failed";
  detail?: string;
}

function buildSubstepStates(events: StreamEvent[]): Record<string, SubstepState> {
  const states: Record<string, SubstepState> = {
    trm_backfill: { status: "pending" },
    flex_ytd: { status: "pending" },
  };

  for (const ev of events) {
    if (ev.step === "done" || ev.step === "error") continue;
    if (ev.step in states) {
      const detail =
        ev.status === "ok" && ev.n_days !== undefined
          ? `${ev.n_days} dias`
          : ev.status === "ok" && ev.n_trades !== undefined
          ? `${ev.n_trades} trades`
          : ev.status === "failed"
          ? ev.error
          : undefined;
      states[ev.step] = {
        status: (ev.status as SubstepState["status"]) ?? "pending",
        detail,
      };
    }
  }

  return states;
}

interface ManualRefreshButtonProps {
  /** Called when the ingest finishes successfully so the log table can refresh */
  onDone?: () => void;
}

export function ManualRefreshButton({ onDone }: ManualRefreshButtonProps) {
  const [jobId, setJobId] = useState<number | null>(null);
  const [triggerError, setTriggerError] = useState<string | null>(null);

  const { events, isDone, error: streamError } = useIngestStream(jobId);

  const { mutate: triggerIngest, isPending: isTriggering } = useMutation({
    mutationFn: () =>
      triggerManualRefreshApiIngestTriggerPost({ kind: "both" }),
    onSuccess: (data) => {
      const d = data;
      if (d?.job_id !== undefined) {
        setTriggerError(null);
        setJobId(d.job_id);
      }
    },
    onError: (err: unknown) => {
      const e = err as { response?: { data?: { detail?: string } } };
      setTriggerError(
        e?.response?.data?.detail ?? "Error al iniciar el refresh"
      );
    },
  });

  // Derive flags from events/isDone in render — avoids the race where two
  // useEffects read each other's stale state when the failed+done events land
  // in the same React batch (then finished would latch true alongside a red row).
  const hasFailed = events.some((ev) => ev.status === "failed");
  const finished = isDone && !hasFailed && !streamError;
  const isRunning = jobId !== null && !isDone && !hasFailed;
  const substepStates = buildSubstepStates(events);
  const showSubsteps = jobId !== null;

  useEffect(() => {
    if (finished) onDone?.();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finished]);

  function handleTrigger() {
    setJobId(null);
    setTriggerError(null);
    triggerIngest();
  }

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Refresh manual</h2>
          <p className="text-sm text-muted-foreground">
            Descarga TRM y Flex YTD ahora, sin esperar el schedule automatico.
          </p>
        </div>
        <Button
          onClick={handleTrigger}
          disabled={isRunning || isTriggering}
          variant={hasFailed || !!streamError ? "outline" : "default"}
        >
          {isTriggering
            ? "Iniciando…"
            : isRunning
            ? "Ejecutando…"
            : hasFailed || streamError
            ? "Reintentar"
            : "Ejecutar ahora"}
        </Button>
      </div>

      {triggerError && (
        <p className="text-sm text-red-600">{triggerError}</p>
      )}

      {showSubsteps && (
        <ul className="space-y-1">
          {(["trm_backfill", "flex_ytd"] as const).map((key) => {
            const s = substepStates[key];
            return (
              <li key={key} className="flex items-center gap-2 text-sm">
                <span className={`text-base ${substepColor(s.status)}`}>
                  {substepIcon(s.status)}
                </span>
                <span>{SUBSTEP_LABELS[key]}</span>
                {s.detail && (
                  <span className={`text-xs ${substepColor(s.status)}`}>
                    — {s.detail}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {finished && (
        <p className="text-sm text-green-600 font-medium">
          ✓ Refresh completado correctamente.
        </p>
      )}

      {(hasFailed || streamError) && (
        <p className="text-sm text-red-600">
          {streamError ?? "Una tarea fallo. Revisa los detalles y reintenta."}
        </p>
      )}
    </section>
  );
}
