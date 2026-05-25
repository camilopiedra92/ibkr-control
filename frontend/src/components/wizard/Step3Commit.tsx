"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { step3CommitApiSetupStep3CommitPost } from "@/lib/api";

interface Props {
  tempIds: string[];
  onCommitted: () => void;
}

/**
 * Step3 Commit — drains the stash on the backend, persisting every staged
 * XML in a single transaction. Pre-condition: all new accounts seen across
 * the stashed XMLs are already configured (the wizard router enforces this
 * via the new-accounts modal before reaching this screen).
 */
export function Step3Commit({ tempIds, onCommitted }: Props) {
  const [error, setError] = useState<string | null>(null);

  const { mutate, isPending } = useMutation({
    mutationFn: () =>
      step3CommitApiSetupStep3CommitPost({ temp_ids: tempIds }),
    onSuccess: () => onCommitted(),
    onError: (err: unknown) => {
      const e = err as {
        response?: {
          data?: {
            detail?:
              | {
                  code?: string;
                  ibkr_account_id?: string;
                  temp_id?: string;
                }
              | Array<{ loc?: unknown; msg?: string; type?: string }>
              | string;
          };
        };
      };
      const detail = e?.response?.data?.detail;
      if (Array.isArray(detail)) {
        setError("Datos inválidos: revisá la lista de XMLs a importar");
        return;
      }
      if (typeof detail === "object" && detail !== null) {
        if (detail.code === "UNRESOLVED_NEW_ACCOUNTS") {
          setError(
            `Falta configurar la cuenta ${detail.ibkr_account_id} antes de importar`,
          );
          return;
        }
        if (detail.code === "TEMP_ID_EXPIRED") {
          setError(
            `El XML ${detail.temp_id} expiró del stash. Volvé a subirlo.`,
          );
          return;
        }
      }
      if (typeof detail === "string") {
        setError(detail);
        return;
      }
      setError("Error al importar");
    },
  });

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold">
        Importar {tempIds.length} XML{tempIds.length === 1 ? "" : "s"}
      </h2>
      <p className="text-sm text-muted-foreground">
        Todo listo para importar los XMLs subidos. Esto persiste trades, cash
        transactions y posiciones de los años cubiertos.
      </p>
      <Button
        onClick={() => {
          setError(null);
          mutate();
        }}
        disabled={isPending}
      >
        {isPending
          ? "Importando…"
          : `Importar ${tempIds.length} XML${tempIds.length === 1 ? "" : "s"}`}
      </Button>
      {error && <p className="text-sm text-red-600">{error}</p>}
    </div>
  );
}
