"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { step1SaveApiSetupStep1SavePost } from "@/lib/api";

interface Step1Props {
  onComplete: () => void;
}

/**
 * Step 1 (redesigned): save Flex token + YTD query id WITHOUT calling IBKR.
 *
 * In the new flow, validation against IBKR happens in Step 2 (account
 * detection). This step is a cheap, fast credential persist — see the
 * wizard redesign spec § Step 1.
 */
export function Step1Credentials({ onComplete }: Step1Props) {
  const [token, setToken] = useState("");
  const [queryId, setQueryId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const { mutate, isPending } = useMutation({
    mutationFn: (data: { token: string; query_id: string }) =>
      step1SaveApiSetupStep1SavePost(data),
    onSuccess: () => {
      onComplete();
    },
    onError: (err: unknown) => {
      const e = err as { response?: { data?: { detail?: string } } };
      setError(e?.response?.data?.detail ?? "No se pudo guardar las credenciales");
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    mutate({ token, query_id: queryId });
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h2 className="text-xl font-semibold">Credenciales IBKR Flex</h2>
      <p className="text-sm text-muted-foreground">
        Pegá tu Flex Token y el Query ID del reporte YTD configurado en IBKR
        Account Management. En el próximo paso vamos a usar estos datos para
        descubrir tus cuentas automáticamente.
      </p>

      <div className="space-y-2">
        <Label htmlFor="token">Flex Token (read-only)</Label>
        <Input
          id="token"
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="Token de 10+ caracteres"
          required
          minLength={10}
          autoComplete="off"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="queryId">YTD Flex Query ID</Label>
        <Input
          id="queryId"
          type="text"
          value={queryId}
          onChange={(e) => setQueryId(e.target.value)}
          placeholder="Ej: 123456"
          required
          pattern="^\d+$"
          inputMode="numeric"
        />
        <p className="text-xs text-muted-foreground">
          Solo dígitos. Lo encontrás en Flex Queries dentro de Account Management.
        </p>
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="flex justify-end">
        <Button type="submit" disabled={isPending}>
          {isPending ? "Guardando…" : "Continuar →"}
        </Button>
      </div>
    </form>
  );
}
