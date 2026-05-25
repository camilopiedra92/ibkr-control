"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { useStep2Detect } from "@/hooks/useStep2Detect";
import type { DetectedAccount } from "@/lib/api";

interface Step2DetectProps {
  /** Callback fired with detected accounts on success. */
  onDetected: (accounts: DetectedAccount[]) => void;
  /** Back button: send the user back to step1 (credential edit). */
  onBack: () => void;
}

/**
 * Step 2 (detect): auto-triggers POST /api/setup/step2/detect on mount,
 * shows a spinner with elapsed seconds, and renders typed-error UI for the
 * 6 error codes mapped by useStep2Detect.
 *
 * Fallback "subir XML manual" link appears after 30 s so a stuck request
 * can be worked around with detect_from_xml.
 */
export function Step2Detect({ onDetected, onBack }: Step2DetectProps) {
  const { detect, detectFromXml, isLoading, elapsedSeconds, error } =
    useStep2Detect();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const triggeredRef = useRef(false);
  const [xmlBusy, setXmlBusy] = useState(false);

  // Auto-trigger detect on mount, but only once (StrictMode double-invokes
  // effects in dev — guard with a ref so we don't fire two parallel requests).
  useEffect(() => {
    if (triggeredRef.current) return;
    triggeredRef.current = true;
    void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function run() {
    const result = await detect();
    if (result) onDetected(result);
  }

  async function handleXmlPicked(file: File) {
    setXmlBusy(true);
    try {
      const result = await detectFromXml(file);
      if (result) onDetected(result);
    } finally {
      setXmlBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold">Detectando tus cuentas IBKR</h2>
      <p className="text-sm text-muted-foreground">
        Estamos consultando Flex Web Service con las credenciales que acabás de
        guardar para descubrir qué cuentas IBKR podés usar en este wizard.
      </p>

      {isLoading && (
        <div className="rounded border p-4 space-y-2">
          <div className="flex items-center gap-3">
            <span
              aria-hidden
              className="inline-block w-4 h-4 rounded-full border-2 border-blue-500 border-t-transparent animate-spin"
            />
            <span className="text-sm">
              Consultando IBKR… ({elapsedSeconds}s)
            </span>
          </div>
          {elapsedSeconds > 5 && (
            <p className="text-xs text-muted-foreground">
              IBKR puede tardar hasta 30 segundos en responder, especialmente
              en horario pico. Reintentamos automáticamente si está ocupado.
            </p>
          )}
          {elapsedSeconds > 30 && (
            <div className="text-xs text-muted-foreground pt-2 border-t">
              ¿No responde?{" "}
              <button
                type="button"
                className="underline hover:text-foreground"
                onClick={() => fileInputRef.current?.click()}
                disabled={xmlBusy}
              >
                Subir un XML manualmente
              </button>{" "}
              para detectar cuentas sin IBKR.
            </div>
          )}
        </div>
      )}

      {error && !isLoading && (
        <div className="rounded border border-red-300 bg-red-50 p-4 space-y-3">
          <ErrorMessage error={error} />
          <div className="flex flex-wrap gap-2">
            {error.code !== "INVALID_TOKEN" && error.code !== "QUERY_NOT_FOUND" && (
              <Button type="button" variant="outline" onClick={() => void run()}>
                Reintentar
              </Button>
            )}
            <Button
              type="button"
              variant="outline"
              onClick={() => fileInputRef.current?.click()}
              disabled={xmlBusy}
            >
              {xmlBusy ? "Procesando XML…" : "Subir XML manualmente"}
            </Button>
            <Button type="button" variant="ghost" onClick={onBack}>
              ← Editar credenciales
            </Button>
          </div>
        </div>
      )}

      <input
        ref={fileInputRef}
        type="file"
        accept=".xml"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void handleXmlPicked(f);
          e.target.value = "";
        }}
      />

      {!isLoading && !error && (
        <div className="flex justify-start">
          <Button type="button" variant="ghost" onClick={onBack}>
            ← Atrás
          </Button>
        </div>
      )}
    </div>
  );
}

function ErrorMessage({
  error,
}: {
  error: ReturnType<typeof useStep2Detect>["error"];
}) {
  if (!error) return null;
  switch (error.code) {
    case "IBKR_BUSY":
      return (
        <p className="text-sm text-red-700">
          IBKR está ocupado ({error.attempts} intentos). Esto suele resolverse
          solo en unos minutos. Probá de nuevo o subí un XML manual.
        </p>
      );
    case "INVALID_TOKEN":
      return (
        <p className="text-sm text-red-700">
          El token Flex es inválido o expiró. Volvé al paso anterior y
          regeneralo en IBKR Account Management.
        </p>
      );
    case "QUERY_NOT_FOUND":
      return (
        <p className="text-sm text-red-700">
          IBKR no encontró ese Query ID. Verificá que el reporte YTD esté
          configurado y activo en Flex Queries.
        </p>
      );
    case "TIMEOUT":
      return (
        <p className="text-sm text-red-700">
          IBKR no respondió a tiempo. Intentá de nuevo o usá la opción de XML
          manual.
        </p>
      );
    case "IBKR_ERROR":
      return (
        <p className="text-sm text-red-700">
          IBKR devolvió un error inesperado: {error.message}
        </p>
      );
    case "UNKNOWN":
    default:
      return (
        <p className="text-sm text-red-700">
          Error inesperado: {error.code === "UNKNOWN" ? error.message : ""}
        </p>
      );
  }
}
