import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { RestatementsPanel } from "./RestatementsPanel";
import type {
  RestatementRead,
  IngestHealthResponse,
  ListRestatementsApiIngestRestatementsGetParams,
} from "@/lib/api";

// Seam: the panel reads health off the shared ["ingest-health"] query (same key
// the rest of Settings uses) and lazily fetches the detail list. Stub both
// generated fetch fns so the UI is driven deterministically (no network/axios).
const getHealth = vi.fn<() => Promise<IngestHealthResponse>>();
const listRestatements =
  vi.fn<
    (
      params?: ListRestatementsApiIngestRestatementsGetParams
    ) => Promise<RestatementRead[]>
  >();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getIngestHealthApiHealthIngestGet: () => getHealth(),
    listRestatementsApiIngestRestatementsGet: (
      params?: ListRestatementsApiIngestRestatementsGetParams
    ) => listRestatements(params),
  };
});

function makeHealth(
  restatements: { recent_count: number; sealed_count: number }
): IngestHealthResponse {
  return {
    sources: [],
    connections: [],
    restatements,
    checked_at: new Date().toISOString(),
  };
}

function makeRow(overrides: Partial<RestatementRead> = {}): RestatementRead {
  return {
    id: 1,
    flex_import_id: 7,
    table_name: "open_position_lots",
    natural_key: { ibkr_account_id: "U99999002", symbol: "ICSH" },
    column_name: "cost_basis_usd",
    old_value: "100.00",
    new_value: "108.75",
    kind: "value_update",
    sealed_year: false,
    detected_at: new Date().toISOString(),
    ...overrides,
  };
}

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <RestatementsPanel />
    </QueryClientProvider>
  );
  return { queryClient };
}

beforeEach(() => {
  vi.clearAllMocks();
  // Default: no detail rows unless a test overrides.
  listRestatements.mockResolvedValue([]);
});

describe("RestatementsPanel — badge", () => {
  it("renders nothing when recent_count is 0", async () => {
    getHealth.mockResolvedValue(makeHealth({ recent_count: 0, sealed_count: 0 }));
    renderPanel();

    // Give the health query a tick to settle, then assert the panel is absent.
    await waitFor(() => {
      expect(getHealth).toHaveBeenCalled();
    });
    expect(screen.queryByText(/restatement/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /Ver detalle/i })).toBeNull();
  });

  it("shows an amber badge when N>0 and M==0", async () => {
    getHealth.mockResolvedValue(makeHealth({ recent_count: 3, sealed_count: 0 }));
    renderPanel();

    const badge = await screen.findByTestId("restatements-badge");
    expect(badge).toHaveTextContent(
      "3 restatements (0 en año sealed) · últimos 7 días"
    );
    expect(badge.className).toMatch(/amber/);
    expect(badge.className).not.toMatch(/red/);
  });

  it("shows a red badge when M>0", async () => {
    getHealth.mockResolvedValue(makeHealth({ recent_count: 4, sealed_count: 2 }));
    renderPanel();

    const badge = await screen.findByTestId("restatements-badge");
    expect(badge).toHaveTextContent(
      "4 restatements (2 en año sealed) · últimos 7 días"
    );
    expect(badge.className).toMatch(/red/);
  });
});

describe("RestatementsPanel — detail table (lazy)", () => {
  it("does not fetch the detail list until the panel is expanded", async () => {
    getHealth.mockResolvedValue(makeHealth({ recent_count: 2, sealed_count: 0 }));
    renderPanel();

    await screen.findByTestId("restatements-badge");
    // Collapsed by default: detail list never queried.
    expect(listRestatements).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /Ver detalle/i }));
    await waitFor(() => expect(listRestatements).toHaveBeenCalled());
  });

  it("renders rows with old → new, type label and sealed chip", async () => {
    getHealth.mockResolvedValue(makeHealth({ recent_count: 2, sealed_count: 1 }));
    listRestatements.mockResolvedValue([
      makeRow({
        id: 1,
        table_name: "open_position_lots",
        column_name: "cost_basis_usd",
        old_value: "100.00",
        new_value: "108.75",
        kind: "value_update",
        sealed_year: true,
      }),
      makeRow({
        id: 2,
        table_name: "closed_lots",
        column_name: "*",
        old_value: "12.00",
        new_value: "8.75",
        kind: "sibling_row",
        sealed_year: false,
      }),
    ]);
    renderPanel();

    await screen.findByTestId("restatements-badge");
    fireEvent.click(screen.getByRole("button", { name: /Ver detalle/i }));

    await waitFor(() => {
      expect(screen.getByText("open_position_lots")).toBeInTheDocument();
    });
    // old → new rendered.
    expect(screen.getByText(/100\.00/)).toBeInTheDocument();
    expect(screen.getByText(/108\.75/)).toBeInTheDocument();
    // Type labels (exhaustive switch on the union).
    expect(screen.getByText("Valor actualizado")).toBeInTheDocument();
    expect(screen.getByText("Fila hermana")).toBeInTheDocument();
    // Sealed chip on the sealed row only.
    expect(screen.getAllByText("Año sealed")).toHaveLength(1);
  });

  it("shows the empty state when the detail list is empty", async () => {
    getHealth.mockResolvedValue(makeHealth({ recent_count: 1, sealed_count: 0 }));
    listRestatements.mockResolvedValue([]);
    renderPanel();

    await screen.findByTestId("restatements-badge");
    fireEvent.click(screen.getByRole("button", { name: /Ver detalle/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/Sin restatements en los últimos registros/i)
      ).toBeInTheDocument();
    });
  });

  it("surfaces backend error detail when the detail list fails", async () => {
    getHealth.mockResolvedValue(makeHealth({ recent_count: 1, sealed_count: 0 }));
    listRestatements.mockRejectedValue({
      response: { data: { detail: "No pude leer el log" } },
    });
    renderPanel();

    await screen.findByTestId("restatements-badge");
    fireEvent.click(screen.getByRole("button", { name: /Ver detalle/i }));

    await waitFor(() => {
      expect(screen.getByText("No pude leer el log")).toBeInTheDocument();
    });
  });
});

describe("RestatementsPanel — pager + filter", () => {
  it("disables next when the page returned fewer than limit rows", async () => {
    getHealth.mockResolvedValue(makeHealth({ recent_count: 2, sealed_count: 0 }));
    listRestatements.mockResolvedValue([makeRow({ id: 1 })]); // < limit (50)
    renderPanel();

    await screen.findByTestId("restatements-badge");
    fireEvent.click(screen.getByRole("button", { name: /Ver detalle/i }));

    await waitFor(() => {
      expect(screen.getByText("open_position_lots")).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /Siguiente/i })).toBeDisabled();
  });

  it("enables next on a full page and advances the offset", async () => {
    getHealth.mockResolvedValue(makeHealth({ recent_count: 60, sealed_count: 0 }));
    const fullPage = Array.from({ length: 50 }, (_, i) => makeRow({ id: i + 1 }));
    listRestatements.mockResolvedValue(fullPage);
    renderPanel();

    await screen.findByTestId("restatements-badge");
    fireEvent.click(screen.getByRole("button", { name: /Ver detalle/i }));

    await waitFor(() =>
      expect(screen.getAllByText("open_position_lots").length).toBe(50)
    );
    const next = screen.getByRole("button", { name: /Siguiente/i });
    expect(next).not.toBeDisabled();

    fireEvent.click(next);
    await waitFor(() => {
      const lastCall = listRestatements.mock.calls.at(-1)?.[0];
      expect(lastCall?.offset).toBe(50);
    });
  });

  it("checking 'Solo año sealed' refetches with sealed_only=true", async () => {
    getHealth.mockResolvedValue(makeHealth({ recent_count: 2, sealed_count: 1 }));
    listRestatements.mockResolvedValue([makeRow({ id: 1 })]);
    renderPanel();

    await screen.findByTestId("restatements-badge");
    fireEvent.click(screen.getByRole("button", { name: /Ver detalle/i }));
    await waitFor(() => expect(listRestatements).toHaveBeenCalled());

    fireEvent.click(screen.getByLabelText(/Solo año sealed/i));
    await waitFor(() => {
      const lastCall = listRestatements.mock.calls.at(-1)?.[0];
      expect(lastCall?.sealed_only).toBe(true);
    });
  });
});
