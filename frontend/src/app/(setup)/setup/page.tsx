"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Stepper } from "@/components/wizard/Stepper";
import { Step1Credentials } from "@/components/wizard/Step1Credentials";
import { Step2Accounts } from "@/components/wizard/Step2Accounts";
import { useSetupState, currentStep } from "@/hooks/useSetupState";

export default function SetupPage() {
  const router = useRouter();
  const { state, refetch, isLoading } = useSetupState();
  // override lets us navigate forward/back without waiting for server refetch
  const [override, setOverride] = useState<1 | 2 | 3 | 4 | null>(null);

  if (isLoading) {
    return (
      <p className="text-sm text-muted-foreground">Cargando estado…</p>
    );
  }

  // If setup already complete, redirect to dashboard
  if (state?.setup_completed_at) {
    router.replace("/dashboard");
    return null;
  }

  const active = override ?? currentStep(state);

  function handleStep1Complete() {
    refetch();
    setOverride(2);
  }

  function handleStep2Complete() {
    refetch();
    setOverride(3);
  }

  return (
    <div>
      <Stepper
        current={active}
        labels={["Credenciales", "Cuentas", "Historicos", "Iniciar"]}
      />

      {active === 1 && (
        <Step1Credentials onComplete={handleStep1Complete} />
      )}
      {active === 2 && (
        <Step2Accounts
          onComplete={handleStep2Complete}
          onBack={() => setOverride(1)}
        />
      )}
      {active === 3 && (
        <div className="text-center py-8 space-y-2">
          <p className="font-medium">Paso 3: Carga de XMLs historicos</p>
          <p className="text-sm text-muted-foreground">
            Implementado en Task 17.
          </p>
        </div>
      )}
      {active === 4 && (
        <div className="text-center py-8 space-y-2">
          <p className="font-medium">Paso 4: Iniciar backfill</p>
          <p className="text-sm text-muted-foreground">
            Implementado en Task 17.
          </p>
        </div>
      )}
    </div>
  );
}
