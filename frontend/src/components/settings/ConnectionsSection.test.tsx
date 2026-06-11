import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ConnectionsSection } from "./ConnectionsSection";
import type { ConnectionRead } from "@/lib/api";

// Mock the generated client functions the section calls. This is the seam:
// the component goes through these named exports, so stubbing them here lets us
// drive the UI deterministically (no network, no axios).
const listConnections = vi.fn<() => Promise<ConnectionRead[]>>();
const createConnection = vi.fn<(body: unknown) => Promise<ConnectionRead>>();
const disableConnection = vi.fn<(id: number) => Promise<ConnectionRead>>();
const enableConnection = vi.fn<(id: number) => Promise<ConnectionRead>>();
const deleteConnection = vi.fn<(id: number) => Promise<void>>();
const rotateToken = vi.fn<(id: number, body: unknown) => Promise<ConnectionRead>>();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listConnectionsApiConnectionsGet: () => listConnections(),
    createConnectionApiConnectionsPost: (body: unknown) => createConnection(body),
    disableConnectionApiConnectionsConnectionIdDisablePost: (id: number) =>
      disableConnection(id),
    enableConnectionApiConnectionsConnectionIdEnablePost: (id: number) =>
      enableConnection(id),
    deleteConnectionApiConnectionsConnectionIdDelete: (id: number) =>
      deleteConnection(id),
    rotateTokenApiConnectionsConnectionIdRotateTokenPost: (
      id: number,
      body: unknown
    ) => rotateToken(id, body),
  };
});

function makeConn(overrides: Partial<ConnectionRead> = {}): ConnectionRead {
  return {
    id: 1,
    institution_code: "ibkr",
    provider_type: "ibkr_flex",
    display_name: "Cuenta personal",
    status: "active",
    status_reason: null,
    last_sync_at: new Date().toISOString(),
    last_sync_status: "ok",
    consecutive_failures: 0,
    query_id: "123456",
    last_rotated_at: new Date().toISOString(),
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

function renderSection() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ConnectionsSection />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ConnectionsSection", () => {
  it("renders one card per connection with the right badge per status", async () => {
    listConnections.mockResolvedValue([
      makeConn({ id: 1, status: "active" }),
      makeConn({ id: 2, status: "degraded" }),
      makeConn({ id: 3, status: "reauth_required" }),
      makeConn({ id: 4, status: "disabled" }),
    ]);
    renderSection();

    await waitFor(() => {
      expect(screen.getByTestId("status-badge-1")).toHaveTextContent("Activa");
    });
    expect(screen.getByTestId("status-badge-2")).toHaveTextContent("Degradada");
    expect(screen.getByTestId("status-badge-3")).toHaveTextContent(
      "Requiere re-autenticación"
    );
    expect(screen.getByTestId("status-badge-4")).toHaveTextContent("Pausada");
  });

  it("reauth_required card shows status_reason + Rotar token CTA", async () => {
    listConnections.mockResolvedValue([
      makeConn({
        id: 9,
        status: "reauth_required",
        status_reason: "1018 token inválido o expirado",
      }),
    ]);
    rotateToken.mockResolvedValue(makeConn({ id: 9, status: "active" }));
    renderSection();

    await waitFor(() => {
      expect(
        screen.getByText("1018 token inválido o expirado")
      ).toBeInTheDocument();
    });
    // Opening the modal proves the Rotar token CTA is wired.
    fireEvent.click(screen.getAllByRole("button", { name: "Rotar token" })[0]);
    expect(screen.getByText("Rotar token Flex")).toBeInTheDocument();
  });

  it("shows Pausar for non-disabled and Reanudar for disabled, calling the right endpoint", async () => {
    listConnections.mockResolvedValue([
      makeConn({ id: 1, status: "active" }),
      makeConn({ id: 2, status: "disabled" }),
    ]);
    disableConnection.mockResolvedValue(makeConn({ id: 1, status: "disabled" }));
    enableConnection.mockResolvedValue(makeConn({ id: 2, status: "active" }));
    renderSection();

    await waitFor(() => {
      expect(screen.getByText("Pausar")).toBeInTheDocument();
    });
    expect(screen.getByText("Reanudar")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Pausar"));
    await waitFor(() => expect(disableConnection).toHaveBeenCalledWith(1));

    fireEvent.click(screen.getByText("Reanudar"));
    await waitFor(() => expect(enableConnection).toHaveBeenCalledWith(2));
  });

  it("add-connection form submits token/query_id/display_name", async () => {
    listConnections.mockResolvedValue([]);
    createConnection.mockResolvedValue(makeConn({ id: 5 }));
    renderSection();

    await waitFor(() => {
      expect(
        screen.getByText(/No hay conexiones configuradas/i)
      ).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/Flex Token/i), {
      target: { value: "tok-1234567890" },
    });
    fireEvent.change(screen.getByLabelText(/YTD Flex Query ID/i), {
      target: { value: "987654" },
    });
    fireEvent.change(screen.getByLabelText(/Nombre de la conexión/i), {
      target: { value: "Mi conexión" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Agregar conexión" }));

    await waitFor(() => {
      expect(createConnection).toHaveBeenCalledWith({
        token: "tok-1234567890",
        query_id: "987654",
        display_name: "Mi conexión",
      });
    });
  });

  it("surfaces backend error detail when add-connection fails (401/400/502)", async () => {
    listConnections.mockResolvedValue([]);
    createConnection.mockRejectedValue({
      response: { data: { detail: "No pude alcanzar IBKR" } },
    });
    renderSection();

    await waitFor(() => {
      expect(
        screen.getByText(/No hay conexiones configuradas/i)
      ).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/Flex Token/i), {
      target: { value: "tok-1234567890" },
    });
    fireEvent.change(screen.getByLabelText(/YTD Flex Query ID/i), {
      target: { value: "987654" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Agregar conexión" }));

    await waitFor(() => {
      expect(screen.getByText("No pude alcanzar IBKR")).toBeInTheDocument();
    });
  });
});
