"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  updateFlexCredentialsApiCredentialsFlexPut,
  FlexCredentialsUpdate,
} from "@/lib/api";

interface RotateTokenModalProps {
  currentQueryId: string;
  // The PUT /api/credentials/flex endpoint returns {"ok": true}, not FlexCredentialsRead.
  // The caller is responsible for refetching updated credentials after success.
  onSuccess: () => void;
  onCancel: () => void;
}

export function RotateTokenModal({
  currentQueryId,
  onSuccess,
  onCancel,
}: RotateTokenModalProps) {
  const [token, setToken] = useState("");
  const [queryId, setQueryId] = useState(currentQueryId);
  const [error, setError] = useState<string | null>(null);

  const { mutate: updateCreds, isPending } = useMutation({
    mutationFn: (payload: FlexCredentialsUpdate) =>
      updateFlexCredentialsApiCredentialsFlexPut(payload),
    onSuccess: () => {
      // The endpoint returns {"ok": true}. Caller refetches credentials metadata.
      onSuccess();
    },
    onError: (err: unknown) => {
      const e = err as { response?: { data?: { detail?: string } } };
      setError(e?.response?.data?.detail ?? "Error al actualizar las credenciales");
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const payload: FlexCredentialsUpdate = {};
    if (token.trim()) payload.token = token.trim();
    if (queryId.trim() && queryId.trim() !== currentQueryId) {
      payload.query_id = queryId.trim();
    }
    if (!payload.token && !payload.query_id) {
      setError("Ingresa al menos el token o un nuevo Query ID");
      return;
    }
    updateCreds(payload);
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
