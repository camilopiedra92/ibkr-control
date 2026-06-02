"use client";

import { useEffect, useState, ReactNode } from "react";
import { useRouter } from "next/navigation";
import { getToken } from "@/lib/auth/storeToken";

export function AuthGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    // Client-only auth gate: reads localStorage (unavailable during SSR/hydration)
    // and performs navigation. setChecked gates render to avoid flashing protected
    // content. This is a legitimate side-effecting effect, not derivable state.
    if (!getToken()) {
      router.replace("/login");
    } else {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- render-gate after client-only auth check
      setChecked(true);
    }
  }, [router]);

  if (!checked) return null;
  return <>{children}</>;
}
