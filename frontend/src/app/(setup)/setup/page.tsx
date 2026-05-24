"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Stepper } from "@/components/wizard/Stepper";
import { Step1Credentials } from "@/components/wizard/Step1Credentials";
import { Step2Accounts } from "@/components/wizard/Step2Accounts";
import { Step3Xmls } from "@/components/wizard/Step3Xmls";
import { Step4Initial } from "@/components/wizard/Step4Initial";
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

  function handleStep3Complete() {
    refetch();
    setOverride(4);
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
        <Step3Xmls
          onComplete={handleStep3Complete}
          onBack={() => setOverride(2)}
        />
      )}
      {active === 4 && (
        <Step4Initial onBack={() => setOverride(3)} />
      )}
    </div>
  );
}
