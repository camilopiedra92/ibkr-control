"use client";

import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import type { AxiosError } from "axios";
import { Button } from "@/components/ui/button";
import { getFlexCredentialsApiCredentialsFlexGet, FlexCredentialsRead } from "@/lib/api";
import { RotateTokenModal } from "./RotateTokenModal";

export function FlexCredentialsSection() {
  const [creds, setCreds] = useState<FlexCredentialsRead | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);

  const { mutate: fetchCreds, isPending } = useMutation({
    mutationFn: () => getFlexCredentialsApiCredentialsFlexGet(),
    onSuccess: (data) => {
      setCreds(data as FlexCredentialsRead);
      setFetchError(null);
    },
    onError: (err) => {
      // 404 = backend signals "no Flex credentials configured yet" — empty state, not an error.
      if ((err as AxiosError).response?.status === 404) {
        setCreds(null);
        setFetchError(null);
        return;
      }
      setFetchError("No se pudo cargar las credenciales");
    },
  });

  useEffect(() => {
    fetchCreds();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleRotateSuccess() {
    // PUT /api/credentials/flex returns {"ok": true}, not the updated credentials.
    // Refetch the metadata to get the updated query_id and last_rotated_at.
    setShowModal(false);
    fetchCreds();
  }

  function formatDate(iso: string | null | undefined): string {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleString("es-CO", {
        dateStyle: "medium",
        timeStyle: "short",
      });
    } catch {
      return iso;
    }
  }

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Credenciales Flex IBKR</h2>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setShowModal(true)}
          disabled={isPending}
        >
          Rotar token
        </Button>
      </div>

      {isPending && (
        <p className="text-sm text-muted-foreground">Cargando…</p>
      )}

      {fetchError && (
        <p className="text-sm text-red-600">{fetchError}</p>
      )}

      {!isPending && !fetchError && !creds && (
        <p className="text-sm text-muted-foreground">
          No hay credenciales configuradas. Usá &quot;Rotar token&quot; para configurar tu Flex Token y Query ID.
        </p>
      )}

      {creds && !isPending && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <dt className="text-muted-foreground">Query ID</dt>
          <dd className="font-mono">{creds.query_id || "—"}</dd>

          <dt className="text-muted-foreground">Configurado</dt>
          <dd>{formatDate(creds.configured_at)}</dd>

          <dt className="text-muted-foreground">Ultimo token</dt>
          <dd>{formatDate(creds.last_rotated_at)}</dd>
        </dl>
      )}

      {showModal && (
        <RotateTokenModal
          currentQueryId={creds?.query_id ?? ""}
          onSuccess={handleRotateSuccess}
          onCancel={() => setShowModal(false)}
        />
      )}
    </section>
  );
}
