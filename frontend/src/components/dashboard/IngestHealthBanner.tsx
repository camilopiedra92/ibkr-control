"use client";

import Link from "next/link";

import type { IngestHealthResponse } from "@/lib/api/generated";


const ONE_DAY_MS = 24 * 60 * 60 * 1000;
const TWO_DAYS_MS = 48 * 60 * 60 * 1000;

type Severity = "ok" | "warning" | "error";


function computeSeverity(health: IngestHealthResponse, now: number = Date.now()): Severity {
  for (const s of health.sources) {
    if ((s.consecutive_failures ?? 0) >= 2) return "error";
    if (s.last_success_at === null || s.last_success_at === undefined) continue;
    const age = now - new Date(s.last_success_at).getTime();
    if (age > TWO_DAYS_MS) return "error";
  }
  for (const s of health.sources) {
    if (s.last_success_at === null || s.last_success_at === undefined) continue;
    const age = now - new Date(s.last_success_at).getTime();
    if (age > ONE_DAY_MS && age <= TWO_DAYS_MS) return "warning";
  }
  return "ok";
}


export function IngestHealthBanner({ health }: { health: IngestHealthResponse }) {
  const severity = computeSeverity(health);
  if (severity === "ok") return null;

  const isError = severity === "error";
  const wrapperClass = isError
    ? "border-l-4 border-red-400 bg-red-50 text-red-900 p-4 mb-4"
    : "border-l-4 border-yellow-400 bg-yellow-50 text-yellow-900 p-4 mb-4";
  const label = isError ? "Falla detectada en ingesta" : "Ingesta sin actualizar";

  return (
    <div role="alert" data-severity={severity} className={wrapperClass}>
      <strong>{label}</strong>
      <p className="text-sm mt-1">
        Verificá el estado en{" "}
        <Link href="/settings#salud-ingesta" className="underline">
          Settings &#x2192; Salud de ingesta
        </Link>
        .
      </p>
    </div>
  );
}
