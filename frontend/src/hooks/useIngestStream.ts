"use client";

import { useEffect, useRef, useState } from "react";
import { getToken } from "@/lib/auth/storeToken";

export interface StreamEvent {
  step: string;
  status?: "running" | "ok" | "partial" | "failed";
  n_days?: number;
  n_trades?: number;
  // W1: partial-failure surfacing for flex_ytd (some connections OK, some failed).
  n_connections_ok?: number;
  n_connections_failed?: number;
  // Phase 2.5 idempotent persister counters (spec A5)
  n_observed_trades?: number;
  n_observed_lots_closed?: number;
  n_observed_open_lots?: number;
  n_observed_cash_tx?: number;
  n_observed_dividends?: number;
  n_observed_transfers?: number;
  n_new_trades?: number;
  n_new_lots_closed?: number;
  n_new_open_lots?: number;
  n_new_cash_tx?: number;
  n_new_dividends?: number;
  n_new_transfers?: number;
  error?: string;
  redirect?: string;
}

export interface IngestStreamState {
  events: StreamEvent[];
  isDone: boolean;
  error: string | null;
}

/**
 * Consumes SSE from /api/ingest/stream/{jobId} via fetch + ReadableStream.
 *
 * EventSource is not used because it doesn't support custom headers, and auth
 * is stored in localStorage (not cookies). fetch() allows adding the
 * Authorization header directly.
 */
export function useIngestStream(jobId: number | null): IngestStreamState {
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [isDone, setIsDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (jobId === null) return;

    // Subscription effect (SSE stream): reset state for the new job before
    // subscribing. Synchronous resets are intrinsic to re-subscribing to an
    // external system on jobId change — the legitimate use of effects.
    /* eslint-disable react-hooks/set-state-in-effect */
    setEvents([]);
    setIsDone(false);
    setError(null);
    /* eslint-enable react-hooks/set-state-in-effect */

    const controller = new AbortController();
    abortRef.current = controller;

    const token = getToken();
    const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "";

    void (async () => {
      try {
        const resp = await fetch(`${apiUrl}/api/ingest/stream/${jobId}`, {
          headers: {
            Accept: "text/event-stream",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          signal: controller.signal,
        });

        if (!resp.ok) {
          setError(`Error del servidor: ${resp.status}`);
          return;
        }

        const reader = resp.body!.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // SSE events are separated by blank lines (\n\n).
          // Each event block has lines: "event: X\n" and "data: Y\n"
          while (buffer.includes("\n\n")) {
            const sepIdx = buffer.indexOf("\n\n");
            const rawEvent = buffer.slice(0, sepIdx);
            buffer = buffer.slice(sepIdx + 2);

            let eventName = "message";
            let dataStr = "";
            for (const line of rawEvent.split("\n")) {
              if (line.startsWith("event:")) {
                eventName = line.slice(6).trim();
              } else if (line.startsWith("data:")) {
                dataStr += line.slice(5).trim();
              }
            }

            if (dataStr) {
              try {
                const payload = JSON.parse(dataStr) as StreamEvent;
                setEvents((prev) => [...prev, payload]);
                if (eventName === "done") {
                  setIsDone(true);
                  return;
                }
              } catch {
                // Malformed JSON — skip event
              }
            }
          }
        }

        // Stream ended without explicit done event
        setIsDone(true);
      } catch (e: unknown) {
        if (e instanceof Error && e.name !== "AbortError") {
          setError(e.message ?? "Conexion con el servidor perdida");
        }
      }
    })();

    return () => {
      controller.abort();
      abortRef.current = null;
    };
  }, [jobId]);

  return { events, isDone, error };
}
