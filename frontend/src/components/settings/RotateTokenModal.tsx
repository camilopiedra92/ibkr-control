"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  rotateTokenApiConnectionsConnectionIdRotateTokenPost,
  type ConnectionRotate,
} from "@/lib/api";

interface RotateTokenModalProps {
  /** Connection whose token is being rotated (POST /api/connections/{id}/rotate-token). */
  connectionId: number;
  currentQueryId: string;
  // The endpoint validates the new token against IBKR before persisting and
  // returns the updated ConnectionRead. The caller refetches the list on success.
  onSuccess: () => void;
  onCancel: () => void;
}

export function RotateTokenModal({
  connectionId,
  currentQueryId,
  onSuccess,
  onCancel,
}: RotateTokenModalProps) {
  const [token, setToken] = useState("");
  const [queryId, setQueryId] = useState(currentQueryId);
  const [error, setError] = useState<string | null>(null);

  const { mutate: rotate, isPending } = useMutation({
    mutationFn: (payload: ConnectionRotate) =>
      rotateTokenApiConnectionsConnectionIdRotateTokenPost(connectionId, payload),
    onSuccess: () => {
      onSuccess();
    },
    onError: (err: unknown) => {
      const e = err as { response?: { data?: { detail?: unknown } } };
      const detail = e?.response?.data?.detail;
      setError(
        typeof detail === "string" ? detail : "Error al rotar el token"
      );
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!token.trim()) {
      setError("Ingresá el nuevo token");
      return;
    }
    // The rotate endpoint requires a token; query_id is optional (only sent if
    // the user changed it).
    const payload: ConnectionRotate = { token: token.trim() };
    if (queryId.trim() && queryId.trim() !== currentQueryId) {
      payload.query_id = queryId.trim();
    }
    rotate(payload);
  }

  return (
    // Overlay backdrop
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div className="bg-card rounded-xl ring-1 ring-foreground/10 p-6 w-full max-w-md space-y-4 shadow-xl">
        <h3 className="text-base font-semibold">Rotar token Flex</h3>
        <p className="text-sm text-muted-foreground">
          El token se valida contra IBKR antes de guardarse. Podes actualizar
          solo el token, solo el Query ID, o ambos.
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="rotate-token">Token Flex (nuevo)</Label>
            <Input
              id="rotate-token"
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="Token generado en IBKR Account Management"
              autoComplete="off"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="rotate-query-id">Query ID</Label>
            <Input
              id="rotate-query-id"
              value={queryId}
              onChange={(e) => setQueryId(e.target.value)}
              placeholder="Query ID de tu Flex Query YTD"
            />
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}

          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              onClick={onCancel}
              disabled={isPending}
            >
              Cancelar
            </Button>
            <Button type="submit" disabled={isPending}>
              {isPending ? "Validando…" : "Guardar"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
