"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * Final wizard splash — shown briefly after POST /api/setup/finish before
 * we redirect to /dashboard. Pure UX affordance so the success state is
 * visible for a moment instead of an instant route swap.
 */
export function StepFinish() {
  const router = useRouter();

  useEffect(() => {
    const t = setTimeout(() => router.replace("/dashboard"), 1500);
    return () => clearTimeout(t);
  }, [router]);

  return (
    <div className="text-center space-y-4 py-12">
      <div className="text-5xl">✓</div>
      <h2 className="text-xl font-semibold">Setup completado</h2>
      <p className="text-sm text-muted-foreground">
        Llevándote al dashboard…
      </p>
    </div>
  );
}
