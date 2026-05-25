"use client";

import { useEffect, useState } from "react";
import { useIngestStream } from "@/hooks/useIngestStream";

interface TrmBackfillBannerProps {
  /** Job id returned by POST /api/setup/step2/save; null hides the banner. */
  jobId: number | null;
  /** Notify the parent so it can clear its stored id when the banner closes. */
  onDismiss?: () => void;
}

/**
 * Wizard-only banner that surfaces the SSE progress of the TRM backfill
 * dispatched after step2/save. Fixes D12 (silent fire-and-forget): when
 * Socrata or the persister fail, the user now sees the error here instead of
 * "Setup completado" with no TRM data.
 *
 * Kept narrow on purpose — the user is moving on to Step 3 (uploads) and we
 * don't want to block them. Terminal states (ok / failed) are dismissable; ok
 * also auto-dismisses after a short delay so it doesn't linger on the
 * Finalizar screen.
 */
export function TrmBackfillBanner({
  jobId,
  onDismiss,
}: TrmBackfillBannerProps) {
  const [dismissed, setDismissed] = useState(false);
  const { events, error: streamError } = useIngestStream(jobId);

  // Reset dismissed flag when the parent gives us a new jobId.
  useEffect(() => {
    setDismissed(false);
  }, [jobId]);

  const trmEvents = events.filter((e) => e.step === "trm_backfill");
  const okEvent = trmEvents.find((e) => e.status === "ok");
  const failedEvent = trmEvents.find((e) => e.status === "failed");

  // Auto-dismiss on success after 8s so it doesn't linger.
  useEffect(() => {
    if (!okEvent || dismissed) return;
    const t = setTimeout(() => {
      setDismissed(true);
      onDismiss?.();
    }, 8000);
    return () => clearTimeout(t);
  }, [okEvent, dismissed, onDismiss]);

  if (jobId === null || dismissed) return null;

  function handleDismiss() {
    setDismissed(true);
    onDismiss?.();
  }

  if (failedEvent || streamError) {
    return (
      <div
        role="alert"
        className="mb-4 rounded border border-red-300 bg-red-50 px-3 py-2 text-sm"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <strong className="text-red-900">TRM histórica: falló</strong>
            <p className="mt-0.5 text-red-800">
              {failedEvent?.error ?? streamError ?? "Error desconocido"}
            </p>
            <p className="mt-1 text-xs text-red-700">
              Podés terminar el setup igual; reintentá desde Settings → Refresh
              manual.
            </p>
          </div>
          <button
            type="button"
            onClick={handleDismiss}
            aria-label="Cerrar"
            className="text-red-700 hover:text-red-900"
          >
            ×
          </button>
        </div>
      </div>
    );
  }

  if (okEvent) {
    return (
      <div className="mb-4 flex items-center justify-between rounded border border-green-300 bg-green-50 px-3 py-2 text-sm text-green-900">
        <span>
          ✓ TRM histórica lista{" "}
          {okEvent.n_days !== undefined ? `(${okEvent.n_days} días)` : ""}
        </span>
        <button
          type="button"
          onClick={handleDismiss}
          aria-label="Cerrar"
          className="text-green-700 hover:text-green-900"
        >
          ×
        </button>
      </div>
    );
  }

  return (
    <div className="mb-4 rounded border border-blue-300 bg-blue-50 px-3 py-2 text-sm text-blue-900">
      <span className="animate-pulse">⏳</span> Descargando TRM histórica de
      DIAN… podés seguir con el wizard.
    </div>
  );
}
