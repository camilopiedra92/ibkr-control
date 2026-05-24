import { ReactNode } from "react";
import { Sidebar } from "@/components/sidebar";
import { AuthGate } from "@/components/auth-gate";

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGate>
      <div className="flex min-h-screen">
        <Sidebar />
        <main className="flex-1 bg-white p-6">{children}</main>
      </div>
    </AuthGate>
  );
}
