import type { ReactNode } from "react";
import { AuthGate } from "@/components/auth-gate";

export default function SetupLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGate>
      <div className="min-h-screen bg-background">
        <header className="border-b py-4 px-6">
          <h1 className="text-lg font-semibold">IBKR Control — Configuracion inicial</h1>
        </header>
        <main className="container mx-auto py-8 max-w-3xl px-4">{children}</main>
      </div>
    </AuthGate>
  );
}
