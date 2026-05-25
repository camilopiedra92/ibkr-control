"use client";

import { useQuery } from "@tanstack/react-query";

import { getIngestHealthApiHealthIngestGet } from "@/lib/api/generated";
import { IngestHealthBanner } from "@/components/dashboard/IngestHealthBanner";


export default function Page() {
  const { data: health } = useQuery({
    queryKey: ["ingest-health"],
    queryFn: () => getIngestHealthApiHealthIngestGet(),
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: true,
  });

  return (
    <div>
      {health && <IngestHealthBanner health={health} />}
      <h1 className="text-2xl font-semibold">Dashboard</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Pantalla pendiente de implementar en fase siguiente.
      </p>
    </div>
  );
}
