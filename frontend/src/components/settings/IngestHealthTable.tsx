"use client";

import type { IngestHealthResponse, IngestLogRead } from "@/lib/api/generated";


function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "Nunca";
  const ms = Date.now() - new Date(iso).getTime();
  const h = Math.floor(ms / (60 * 60 * 1000));
  if (h < 1) return "<1h";
  if (h < 24) return `${h}h`;
  const d = Math.floor(h / 24);
  return `${d}d`;
}


function Sparkline({ logs, source }: { logs: IngestLogRead[]; source: "flex" | "trm" }) {
  const filtered = logs
    .filter((l) => (source === "flex" ? l.job_kind === "flex" : l.job_kind === "trm"))
    .slice(0, 30)
    .reverse();  // oldest left, newest right

  if (filtered.length === 0) {
    return <span className="text-xs text-muted-foreground">—</span>;
  }

  const barWidth = 3;
  const gap = 1;
  const totalWidth = filtered.length * (barWidth + gap);
  const height = 18;

  return (
    <svg width={totalWidth} height={height} aria-label={`Últimos ${filtered.length} runs de ${source}`}>
      {filtered.map((log, i) => {
        const x = i * (barWidth + gap);
        const isOk = log.status === "ok";
        const fillColor = isOk ? "#10b981" : "#ef4444";
        return (
          <rect
            key={log.id}
            x={x}
            y={0}
            width={barWidth}
            height={height}
            fill={fillColor}
          />
        );
      })}
    </svg>
  );
}


export function IngestHealthTable({
  health,
  recentLogs,
}: {
  health: IngestHealthResponse;
  recentLogs: IngestLogRead[];
}) {
  return (
    <table className="w-full text-sm border-collapse">
      <thead>
        <tr className="border-b">
          <th className="text-left py-2 pr-4">Fuente</th>
          <th className="text-left py-2 pr-4">Último éxito</th>
          <th className="text-left py-2 pr-4">Última falla</th>
          <th className="text-left py-2 pr-4">Fallas consec.</th>
          <th className="text-left py-2 pr-4">Último error</th>
          <th className="text-left py-2">Últimos runs</th>
        </tr>
      </thead>
      <tbody>
        {health.sources.map((s) => (
          <tr key={s.source} className="border-b">
            <td className="py-2 pr-4 font-mono">{s.source}</td>
            <td className="py-2 pr-4">{formatRelative(s.last_success_at)}</td>
            <td className="py-2 pr-4">{formatRelative(s.last_failure_at)}</td>
            <td className="py-2 pr-4">{s.consecutive_failures ?? 0}</td>
            <td className="py-2 pr-4 max-w-xs truncate" title={s.last_error ?? ""}>
              {s.last_error ?? "—"}
            </td>
            <td className="py-2">
              <Sparkline logs={recentLogs} source={s.source} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
