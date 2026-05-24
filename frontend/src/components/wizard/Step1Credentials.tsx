"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { step1ValidateApiSetupStep1ValidatePost } from "@/lib/api";

interface Step1Props {
  onComplete: () => void;
}

export function Step1Credentials({ onComplete }: Step1Props) {
  const [token, setToken] = useState("");
  const [queryId, setQueryId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const { mutate, isPending } = useMutation({
    mutationFn: (data: { token: string; query_id: string }) =>
      step1ValidateApiSetupStep1ValidatePost(data),
    onSuccess: () => {
      onComplete();
    },
    onError: (err: unknown) => {
      const e = err as { response?: { data?: { detail?: string } } };
      setError(e?.response?.data?.detail ?? "Error desconocido al validar credenciales");
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
        Genera el token en Account Management &rarr; Settings &rarr; Account Settings &rarr;{" "}
        Flex Web Service. Necesitas tambien el ID del Flex Query &ldquo;Year to Date&rdquo; del
        ano actual.
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
          pattern="\d+"
        />
        <p className="text-xs text-muted-foreground">
          Solo digitos. Lo encontras en Flex Queries dentro de Account Management.
        </p>
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="flex justify-end">
        <Button type="submit" disabled={isPending}>
          {isPending ? "Validando con IBKR…" : "Continuar →"}
        </Button>
      </div>
    </form>
  );
}
