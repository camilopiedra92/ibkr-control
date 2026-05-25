"use client";

import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { step2SaveApiSetupStep2SavePost } from "@/lib/api";
import type { DetectedAccount } from "@/lib/api";

interface Step2ConfigureAccountsProps {
  detected: DetectedAccount[];
  onComplete: () => void;
  onBack: () => void;
}

interface Row {
  ibkr_account_id: string;
  alias: string;
  pct: string;
  hint: string | null;
}

function buildHint(a: DetectedAccount): string | null {
  const parts: string[] = [];
  if (a.account_type) parts.push(a.account_type);
  if (a.account_holder) parts.push(a.account_holder);
  return parts.length > 0 ? parts.join(" · ") : null;
}

/**
 * Step 2 (configure): the user gets a pre-populated table of the accounts we
 * detected through Flex (or XML). Alias defaults to `suggested_alias` from
 * the backend; pct defaults to 1.0000 (100%). The user only needs to lower
 * pct on shared accounts (joint, family) before saving.
 */
export function Step2ConfigureAccounts({
  detected,
  onComplete,
  onBack,
}: Step2ConfigureAccountsProps) {
  const initialRows = useMemo<Row[]>(
    () =>
      detected.map((a) => ({
        ibkr_account_id: a.ibkr_account_id,
        alias: a.suggested_alias ?? "",
        pct: "1.0000",
        hint: buildHint(a),
      })),
    [detected],
  );

  const [rows, setRows] = useState<Row[]>(initialRows);
  const [error, setError] = useState<string | null>(null);

  const { mutate, isPending } = useMutation({
    mutationFn: (payload: Row[]) =>
      step2SaveApiSetupStep2SavePost({
        accounts: payload.map((r) => ({
          ibkr_account_id: r.ibkr_account_id,
          alias: r.alias || null,
          pct: r.pct,
        })),
      }),
    onSuccess: () => onComplete(),
    onError: (err: unknown) => {
      const e = err as { response?: { data?: { detail?: string } } };
      setError(e?.response?.data?.detail ?? "Error al guardar cuentas");
    },
  });

  function updateRow(i: number, field: "alias" | "pct", value: string) {
    setRows((prev) =>
      prev.map((r, idx) => (idx === i ? { ...r, [field]: value } : r)),
    );
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    mutate(rows);
  }

  if (rows.length === 0) {
    return (
      <div className="space-y-4">
        <h2 className="text-xl font-semibold">Configurar cuentas detectadas</h2>
        <p className="text-sm text-muted-foreground">
          IBKR no devolvió ninguna cuenta. Volvé atrás e intentá de nuevo o
          subí un XML manual.
        </p>
        <Button type="button" variant="ghost" onClick={onBack}>
          ← Atrás
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h2 className="text-xl font-semibold">Configurar cuentas detectadas</h2>
      <p className="text-sm text-muted-foreground">
        Detectamos {rows.length} cuenta{rows.length === 1 ? "" : "s"}. Si
        compartís alguna (ej. cuenta conjunta), bajá el % a tu porción real.
        El porcentaje se usa para calcular el patrimonio y los impuestos a tu
        nombre.
      </p>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b">
              <th className="text-left py-2 pr-2 font-medium">Account ID</th>
              <th className="text-left py-2 pr-2 font-medium">Alias</th>
              <th className="text-left py-2 pr-2 font-medium">% Tuyo (0–1)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={row.ibkr_account_id} className="border-b align-top">
                <td className="py-2 pr-2">
                  <div className="font-mono text-sm">{row.ibkr_account_id}</div>
                  {row.hint && (
                    <div className="text-xs text-muted-foreground mt-1">
                      {row.hint}
                    </div>
                  )}
                </td>
                <td className="py-2 pr-2">
                  <Input
                    value={row.alias}
                    onChange={(e) => updateRow(i, "alias", e.target.value)}
                    placeholder="Ej: Personal Swing"
                  />
                </td>
                <td className="py-2 pr-2">
                  <Input
                    value={row.pct}
                    type="number"
                    step="0.0001"
                    min="0"
                    max="1"
                    onChange={(e) => updateRow(i, "pct", e.target.value)}
                    required
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="flex justify-between">
        <Button type="button" variant="ghost" onClick={onBack}>
          ← Atrás
        </Button>
        <Button type="submit" disabled={isPending}>
          {isPending ? "Guardando…" : "Continuar →"}
        </Button>
      </div>
    </form>
  );
}
