"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { step2SaveApiSetupStep2SavePost } from "@/lib/api";

interface AccountRow {
  ibkr_account_id: string;
  alias: string;
  pct: string;
}

interface Step2Props {
  onComplete: () => void;
  onBack: () => void;
}

const DEFAULT_ACCOUNTS: AccountRow[] = [
  { ibkr_account_id: "U99999001", alias: "Conjunta Joint Holder", pct: "0.5000" },
  { ibkr_account_id: "U99999002", alias: "Personal Swing", pct: "1.0000" },
  { ibkr_account_id: "U99999003", alias: "Personal Futuros", pct: "1.0000" },
];

export function Step2Accounts({ onComplete, onBack }: Step2Props) {
  const [accounts, setAccounts] = useState<AccountRow[]>(DEFAULT_ACCOUNTS);
  const [error, setError] = useState<string | null>(null);

  const { mutate, isPending } = useMutation({
    mutationFn: (accounts: AccountRow[]) =>
      step2SaveApiSetupStep2SavePost({
        accounts: accounts.map((r) => ({
          ibkr_account_id: r.ibkr_account_id,
          alias: r.alias || null,
          pct: r.pct,
        })),
      }),
    onSuccess: () => {
      onComplete();
    },
    onError: (err: unknown) => {
      const e = err as { response?: { data?: { detail?: string } } };
      setError(e?.response?.data?.detail ?? "Error al guardar cuentas");
    },
  });

  function updateRow(i: number, field: keyof AccountRow, value: string) {
    setAccounts((prev) => prev.map((r, idx) => (idx === i ? { ...r, [field]: value } : r)));
  }

  function addRow() {
    setAccounts((prev) => [...prev, { ibkr_account_id: "U", alias: "", pct: "1.0000" }]);
  }

  function removeRow(i: number) {
    if (accounts.length <= 1) return;
    setAccounts((prev) => prev.filter((_, idx) => idx !== i));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    mutate(accounts);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h2 className="text-xl font-semibold">Cuentas IBKR + Participaciones</h2>
      <p className="text-sm text-muted-foreground">
        Si compartes alguna cuenta (ej. cuenta conjunta), baja el % a tu porcion real.
        El porcentaje se usa para calcular el patrimonio y los impuestos a tu nombre.
      </p>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b">
              <th className="text-left py-2 pr-2 font-medium">Account ID</th>
              <th className="text-left py-2 pr-2 font-medium">Alias</th>
              <th className="text-left py-2 pr-2 font-medium">% Tuyo (0–1)</th>
              <th className="w-10"></th>
            </tr>
          </thead>
          <tbody>
            {accounts.map((row, i) => (
              <tr key={i} className="border-b">
                <td className="py-2 pr-2">
                  <Input
                    value={row.ibkr_account_id}
                    onChange={(e) => updateRow(i, "ibkr_account_id", e.target.value)}
                    placeholder="U12345678"
                    pattern="^U\d{8}$"
                    required
                    className="font-mono"
                  />
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
                <td className="py-2 text-right">
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => removeRow(i)}
                    disabled={accounts.length <= 1}
                    className="text-muted-foreground hover:text-red-500 px-2"
                    aria-label="Eliminar fila"
                  >
                    ✗
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Button type="button" variant="outline" onClick={addRow} className="w-full">
        + Agregar cuenta
      </Button>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="flex justify-between">
        <Button type="button" variant="ghost" onClick={onBack}>
          ← Atras
        </Button>
        <Button type="submit" disabled={isPending}>
          {isPending ? "Guardando…" : "Continuar →"}
        </Button>
      </div>
    </form>
  );
}
