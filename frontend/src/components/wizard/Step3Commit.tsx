"use client";

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
  const { mutate, isPending, error } = useMutation({
    mutationFn: () =>
      step3CommitApiSetupStep3CommitPost({ temp_ids: tempIds }),
    onSuccess: () => onCommitted(),
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
      <Button onClick={() => mutate()} disabled={isPending}>
        {isPending
          ? "Importando…"
          : `Importar ${tempIds.length} XML${tempIds.length === 1 ? "" : "s"}`}
      </Button>
      {error && <p className="text-sm text-red-600">Error al importar</p>}
    </div>
  );
}
