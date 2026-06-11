import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ManualRefreshButton } from "./ManualRefreshButton";
import type { StreamEvent } from "@/hooks/useIngestStream";

// Drive the SSE hook deterministically: each test sets the events/isDone the
// hook should report. This mirrors the backend contract that _run_manual emits
// (status='partial' + n_connections_ok/failed) — the same keys the test in
// backend/tests/api/test_ingest.py locks (SSE drift lesson).
const streamState: { events: StreamEvent[]; isDone: boolean; error: string | null } = {
  events: [],
  isDone: false,
  error: null,
};

vi.mock("@/hooks/useIngestStream", () => ({
  useIngestStream: () => streamState,
}));

const triggerMock = vi.fn<() => Promise<{ job_id: number }>>();
vi.mock("@/lib/api", () => ({
  triggerManualRefreshApiIngestTriggerPost: () => triggerMock(),
}));

function renderButton() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ManualRefreshButton />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  streamState.events = [];
  streamState.isDone = false;
  streamState.error = null;
  triggerMock.mockResolvedValue({ job_id: 1 });
});

describe("ManualRefreshButton — partial failure", () => {
  it("shows an amber warning and NOT the green all-clear when status=partial", async () => {
    streamState.events = [
      { step: "trm_backfill", status: "ok", n_days: 5 },
      {
        step: "flex_ytd",
        status: "partial",
        n_connections_ok: 1,
        n_connections_failed: 2,
      },
      { step: "done" },
    ];
    streamState.isDone = true;

    renderButton();
    // Kick off the job so jobId is set and the stream state is consumed.
    fireEvent.click(screen.getByRole("button", { name: "Ejecutar ahora" }));

    await waitFor(() => {
      expect(
        screen.getByText(/Completado con advertencias \(2 conexión\/es fallaron\)/i)
      ).toBeInTheDocument();
    });
    expect(
      screen.getByText(/Revisá Conexiones en Settings/i)
    ).toBeInTheDocument();
    // The green all-clear must NOT appear on a partial run.
    expect(
      screen.queryByText(/Refresh completado correctamente/i)
    ).toBeNull();
  });

  it("shows the green all-clear when everything is ok", async () => {
    streamState.events = [
      { step: "trm_backfill", status: "ok", n_days: 5 },
      { step: "flex_ytd", status: "ok", n_connections_ok: 2 },
      { step: "done" },
    ];
    streamState.isDone = true;

    renderButton();
    fireEvent.click(screen.getByRole("button", { name: "Ejecutar ahora" }));

    await waitFor(() => {
      expect(
        screen.getByText(/Refresh completado correctamente/i)
      ).toBeInTheDocument();
    });
    expect(
      screen.queryByText(/Completado con advertencias/i)
    ).toBeNull();
  });
});

describe("ManualRefreshButton — restatements (W3)", () => {
  it("surfaces the restatement line when ok carries n_restatements > 0", async () => {
    streamState.events = [
      { step: "trm_backfill", status: "ok", n_days: 5 },
      { step: "flex_ytd", status: "ok", n_connections_ok: 2, n_restatements: 3 },
      { step: "done" },
    ];
    streamState.isDone = true;

    renderButton();
    fireEvent.click(screen.getByRole("button", { name: "Ejecutar ahora" }));

    await waitFor(() => {
      expect(
        screen.getByText(/3 valor\(es\) restateado\(s\) — revisá Salud de ingesta/i)
      ).toBeInTheDocument();
    });
  });

  it("does NOT surface the line when n_restatements is 0 or absent", async () => {
    streamState.events = [
      { step: "trm_backfill", status: "ok", n_days: 5 },
      { step: "flex_ytd", status: "ok", n_connections_ok: 2, n_restatements: 0 },
      { step: "done" },
    ];
    streamState.isDone = true;

    renderButton();
    fireEvent.click(screen.getByRole("button", { name: "Ejecutar ahora" }));

    await waitFor(() => {
      expect(
        screen.getByText(/Refresh completado correctamente/i)
      ).toBeInTheDocument();
    });
    expect(screen.queryByText(/valor\(es\) restateado/i)).toBeNull();
  });
});
