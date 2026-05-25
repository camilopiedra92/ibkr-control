"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { step3SaveNewAccountsApiSetupStep3SaveNewAccountsPost } from "@/lib/api";
import type { DetectedAccount } from "@/lib/api";

interface Props {
  accounts: DetectedAccount[];
  onSaved: () => void;
}

/**
 * Step3 — modal-style screen prompting the user to provide alias + pct for
 * accounts detected only in stashed XMLs (i.e. not already configured in
 * step2). After save, the wizard advances to step3_commit.
 */
export function Step3NewAccountsModal({ accounts, onSaved }: Props) {
  const [rows, setRows] = useState(
    accounts.map((a) => ({
      ibkr_account_id: a.ibkr_account_id,
      alias: a.suggested_alias ?? "",
      pct: "1.0000",
    })),
  );
  const [error, setError] = useState<string | null>(null);

  const { mutate, isPending } = useMutation({
    mutationFn: () =>
      step3SaveNewAccountsApiSetupStep3SaveNewAccountsPost({
        accounts: rows.map((r) => ({
          ibkr_account_id: r.ibkr_account_id,
          alias: r.alias || null,
          pct: r.pct,
        })),
      }),
    onSuccess: () => onSaved(),
    onError: (err: unknown) => {
      const e = err as { response?: { data?: { detail?: string } } };
      setError(e?.response?.data?.detail ?? "Error al guardar cuentas nuevas");
    },
  });

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold">Cuentas nuevas detectadas</h2>
      <p className="text-sm text-muted-foreground">
        Los XMLs que subiste tienen {accounts.length} cuenta
        {accounts.length === 1 ? "" : "s"} que no estaban configuradas. Llená
        alias + % para continuar.
      </p>

      <div className="space-y-3">
        {rows.map((row, i) => (
          <div
            key={row.ibkr_account_id}
            className="rounded border p-3 space-y-2"
          >
            <div className="font-mono text-sm">{row.ibkr_account_id}</div>
            <div className="grid grid-cols-2 gap-2">
              <Input
                value={row.alias}
                onChange={(e) =>
                  setRows((prev) =>
                    prev.map((r, idx) =>
                      idx === i ? { ...r, alias: e.target.value } : r,
                    ),
                  )
                }
                placeholder="Alias"
              />
              <Input
                type="number"
                step="0.0001"
                min="0"
                max="1"
                value={row.pct}
                onChange={(e) =>
                  setRows((prev) =>
                    prev.map((r, idx) =>
                      idx === i ? { ...r, pct: e.target.value } : r,
                    ),
                  )
                }
              />
            </div>
          </div>
        ))}
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <Button onClick={() => mutate()} disabled={isPending}>
        {isPending ? "Guardando…" : "Continuar →"}
      </Button>
    </div>
  );
}
