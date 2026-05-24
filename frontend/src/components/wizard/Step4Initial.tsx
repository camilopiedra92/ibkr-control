"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { step4StartApiSetupStep4StartPost } from "@/lib/api";
import { useIngestStream, StreamEvent } from "@/hooks/useIngestStream";

interface Step4Props {
  onBack: () => void;
}

const SUBSTEP_LABELS: Record<string, string> = {
  trm_backfill: "Descarga TRM historica (DIAN)",
  flex_ytd: "Descarga Flex YTD de IBKR",
  done: "Configuracion completa",
};

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
          ? `${ev.n_days} dias importados`
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

export function Step4Initial({ onBack }: Step4Props) {
  const router = useRouter();
  const [jobId, setJobId] = useState<number | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [hasFailed, setHasFailed] = useState(false);

  const { events, isDone, error: streamError } = useIngestStream(jobId);

  const { mutate: startJob, isPending: isStarting } = useMutation({
    mutationFn: () => step4StartApiSetupStep4StartPost(),
    onSuccess: (data) => {
      const d = data as { job_id?: number };
      if (d?.job_id !== undefined) {
        setJobId(d.job_id);
      }
    },
    onError: (err: unknown) => {
      const e = err as { response?: { data?: { detail?: string } } };
      setStartError(e?.response?.data?.detail ?? "Error al iniciar la configuracion");
    },
  });

  // Auto-start job when component mounts
  useEffect(() => {
    startJob();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Detect failure from stream events
  useEffect(() => {
    const anyFailed = events.some((ev) => ev.status === "failed");
    if (anyFailed) setHasFailed(true);
  }, [events]);

  // Redirect to dashboard when done successfully
  useEffect(() => {
    if (!isDone || hasFailed) return;
    const doneEvent = events.find((ev) => ev.step === "done");
    const target = doneEvent?.redirect ?? "/dashboard";
    // Short delay so user can see the completed state
    const timer = setTimeout(() => {
      router.replace(target);
    }, 1500);
    return () => clearTimeout(timer);
  }, [isDone, hasFailed, events, router]);

  const substepStates = buildSubstepStates(events);

  function handleRetry() {
    setJobId(null);
    setStartError(null);
    setHasFailed(false);
    startJob();
  }

  const isRunning = jobId !== null && !isDone && !hasFailed;

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold">Descarga inicial de datos</h2>
      <p className="text-sm text-muted-foreground">
        Vamos a descargar la TRM historica de la DIAN y los datos Flex YTD de IBKR.
        Esto puede tomar unos minutos.
      </p>

      {/* Progress list */}
      <ul className="space-y-2">
        {(["trm_backfill", "flex_ytd"] as const).map((key) => {
          const s = substepStates[key];
          return (
            <li key={key} className="flex items-start gap-3 py-2 border-b last:border-0">
              <span className={`text-lg mt-0.5 ${substepColor(s.status)}`}>
                {substepIcon(s.status)}
              </span>
              <div className="flex-1">
                <p className="text-sm font-medium">{SUBSTEP_LABELS[key]}</p>
                {s.detail && (
                  <p className={`text-xs mt-0.5 ${substepColor(s.status)}`}>{s.detail}</p>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      {/* Final status messages */}
      {isDone && !hasFailed && (
        <p className="text-sm text-green-600 font-medium">
          ✓ Configuracion completada. Redirigiendo al dashboard…
        </p>
      )}

      {(hasFailed || streamError) && (
        <p className="text-sm text-red-600">
          {streamError ?? "Una tarea fallo. Revisa los detalles y reintenta."}
        </p>
      )}

      {startError && (
        <p className="text-sm text-red-600">{startError}</p>
      )}

      {/* Actions */}
      <div className="flex justify-between">
        <Button
          type="button"
          variant="ghost"
          onClick={onBack}
          disabled={isRunning || isStarting}
        >
          ← Atras
        </Button>

        {(hasFailed || streamError || startError) && (
          <Button type="button" onClick={handleRetry} disabled={isStarting}>
            {isStarting ? "Iniciando…" : "Reintentar"}
          </Button>
        )}

        {isRunning && (
          <Button type="button" disabled>
            Descargando…
          </Button>
        )}

        {isDone && !hasFailed && (
          <Button type="button" disabled>
            ✓ Listo
          </Button>
        )}
      </div>
    </div>
  );
}
