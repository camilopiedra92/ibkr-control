"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { clearToken } from "@/lib/auth/storeToken";

const items = [
  { href: "/dashboard", label: "Dashboard", icon: "📊" },
  { href: "/lots", label: "Lotes abiertos", icon: "📦" },
  { href: "/closed", label: "Cerrados", icon: "✓" },
  { href: "/alerts", label: "Alertas 730d", icon: "⚠" },
  { href: "/simulator", label: "Simulador", icon: "🧮" },
  { href: "/dividends", label: "Dividendos", icon: "💰" },
  { href: "/patrimonio", label: "Patrimonio", icon: "🏦" },
  { href: "/form160", label: "Form 160", icon: "🌍" },
  { href: "/report", label: "Reporte Form 210", icon: "📋" },
  { href: "/settings", label: "Settings", icon: "⚙" },
];

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();

  function logout() {
    clearToken();
    router.replace("/login");
  }

  return (
    <aside className="flex h-screen w-56 flex-col border-r bg-slate-50 p-3">
      <div className="px-2 pb-4 text-sm font-semibold">IBKR Control</div>
      <nav className="flex-1 space-y-1">
        {items.map((it) => {
          const active = pathname?.startsWith(it.href);
          return (
            <Link
              key={it.href}
              href={it.href}
              className={`flex items-center gap-2 rounded px-3 py-2 text-sm ${
                active ? "bg-blue-500 text-white" : "text-slate-700 hover:bg-slate-200"
              }`}
            >
              <span className="w-5 text-center">{it.icon}</span>
              <span>{it.label}</span>
            </Link>
          );
        })}
      </nav>
      <button
        onClick={logout}
        className="mt-2 rounded px-3 py-2 text-left text-sm text-slate-600 hover:bg-slate-200"
      >
        Salir
      </button>
    </aside>
  );
}
