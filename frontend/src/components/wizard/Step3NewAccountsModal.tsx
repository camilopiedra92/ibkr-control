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

  const validatePct = (raw: string): boolean => {
    const n = Number(raw);
    if (!Number.isFinite(n)) return false;
    if (n < 0 || n > 1) return false;
    // Up to 4 decimals (matches Numeric(5,4) backend column).
    const decimals = raw.includes(".") ? raw.split(".")[1].length : 0;
    if (decimals > 4) return false;
    return true;
  };

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
      const e = err as {
        response?: {
          data?: {
            detail?:
              | { code?: string; ibkr_account_id?: string }
              | Array<{ loc?: unknown; msg?: string; type?: string }>
              | string;
          };
        };
      };
      const detail = e?.response?.data?.detail;
      if (Array.isArray(detail)) {
        // Pydantic 422 — list of validation errors.
        setError("Datos inválidos: verificá alias y porcentaje");
        return;
      }
      if (typeof detail === "object" && detail !== null) {
        if (detail.code === "SHADOW_ACCOUNT_REJECTED") {
          setError(
            `Cuenta ${detail.ibkr_account_id} es shadow (F-suffix), no se puede configurar`,
          );
          return;
        }
      }
      if (typeof detail === "string") {
        setError(detail);
        return;
      }
      setError("Error al guardar cuentas nuevas");
    },
  });

  const handleSubmit = () => {
    setError(null);
    const invalid = rows.find((r) => !validatePct(r.pct));
    if (invalid) {
      setError(
        `Porcentaje inválido para ${invalid.ibkr_account_id}: debe estar entre 0 y 1 con hasta 4 decimales`,
      );
      return;
    }
    mutate();
  };

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

      <Button onClick={handleSubmit} disabled={isPending}>
        {isPending ? "Guardando…" : "Continuar →"}
      </Button>
    </div>
  );
}
