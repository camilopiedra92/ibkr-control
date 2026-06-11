"use client";

import { useEffect, useState } from "react";
import { isAxiosError } from "axios";
import { useQuery } from "@tanstack/react-query";
import { axiosInstance } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { ConnectionsSection } from "@/components/settings/ConnectionsSection";
import { XmlUploadSection } from "@/components/settings/XmlUploadSection";
import { IngestLogTable } from "@/components/settings/IngestLogTable";
import { ManualRefreshButton } from "@/components/settings/ManualRefreshButton";
import { IngestHealthTable } from "@/components/settings/IngestHealthTable";
import {
  getIngestHealthApiHealthIngestGet,
  listLogsApiIngestLogsGet,
} from "@/lib/api/generated";

interface Settings {
  marginal_rate: string;
  timezone: string;
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [rate, setRate] = useState("");
  const [tz, setTz] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Used to trigger a re-fetch in IngestLogTable after a manual refresh
  const [logRefreshKey, setLogRefreshKey] = useState(0);

  const { data: health } = useQuery({
    queryKey: ["ingest-health"],
    queryFn: () => getIngestHealthApiHealthIngestGet(),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: true,
  });
  const { data: recentLogs } = useQuery({
    queryKey: ["ingest-logs-recent"],
    queryFn: () => listLogsApiIngestLogsGet({ limit: 30 }),
    staleTime: 5 * 60 * 1000,
  });

  useEffect(() => {
    axiosInstance
      .get<Settings>("/api/settings")
      .then((r) => {
        setSettings(r.data);
        setRate(r.data.marginal_rate);
        setTz(r.data.timezone);
      })
      .catch((e: unknown) => {
        if (isAxiosError(e) && e.response?.status === 401) return;
        setLoadError("No pude cargar settings. Reintentá en unos segundos.");
      });
  }, []);

  async function save() {
    setSaving(true);
    setMessage(null);
    try {
      const r = await axiosInstance.patch<Settings>("/api/settings", {
        marginal_rate: rate,
        timezone: tz,
      });
      setSettings(r.data);
      setMessage("Guardado");
    } catch {
      setMessage("Error al guardar");
    } finally {
      setSaving(false);
    }
  }

  if (loadError) return <p className="text-sm text-red-600">{loadError}</p>;
  if (!settings) return <p className="text-sm text-muted-foreground">Cargando…</p>;

  return (
    <div className="max-w-2xl space-y-8">
      <h1 className="text-2xl font-semibold">Settings</h1>

      {/* Phase 1: User preferences */}
      <section className="max-w-md space-y-6">
        <div className="space-y-2">
          <Label htmlFor="rate">
            Tarifa marginal RO (0–1, ej. 0.39 para 39%)
          </Label>
          <Input
            id="rate"
            value={rate}
            onChange={(e) => setRate(e.target.value)}
            placeholder="0.39"
          />
          <p className="text-xs text-muted-foreground">
            Usada por el simulador para estimar impuesto cuando un cierre es Renta Ordinaria.
          </p>
        </div>
        <div className="space-y-2">
          <Label htmlFor="tz">Timezone</Label>
          <Input id="tz" value={tz} onChange={(e) => setTz(e.target.value)} />
        </div>
        {message && <p className="text-sm">{message}</p>}
        <Button onClick={save} disabled={saving}>
          {saving ? "Guardando…" : "Guardar"}
        </Button>
      </section>

      <Separator />

      {/* W1: Conexiones IBKR (reemplaza Flex credentials) */}
      <ConnectionsSection />

      <Separator />

      {/* Phase 2: XML upload */}
      <XmlUploadSection />

      <Separator />

      {/* Phase 2: Manual refresh */}
      <ManualRefreshButton onDone={() => setLogRefreshKey((k) => k + 1)} />

      <Separator />

      {/* Phase 2: Ingest log */}
      <IngestLogTable refreshKey={logRefreshKey} />

      <Separator />

      {/* Phase 2.6: Ingest health */}
      <section id="salud-ingesta" className="space-y-3">
        <h2 className="text-lg font-medium">Salud de ingesta</h2>
        {health && recentLogs ? (
          <IngestHealthTable health={health} recentLogs={recentLogs} />
        ) : (
          <p className="text-sm text-muted-foreground">Cargando…</p>
        )}
      </section>
    </div>
  );
}
