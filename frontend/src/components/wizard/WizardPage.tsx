"use client";

import { useMemo, useState } from "react";
import { Stepper } from "@/components/wizard/Stepper";
import { Step1Credentials } from "@/components/wizard/Step1Credentials";
import { Step2Detect } from "@/components/wizard/Step2Detect";
import { Step2ConfigureAccounts } from "@/components/wizard/Step2ConfigureAccounts";
import { StepFinish } from "@/components/wizard/StepFinish";
import { useSetupState } from "@/hooks/useSetupState";
import {
  useWizardScreen,
  type WizardScreen,
  type WizardTransientState,
} from "@/hooks/useWizardNav";
import type { DetectedAccount } from "@/lib/api";

const WIZARD_LABELS: [string, string, string, string] = [
  "Credenciales",
  "Cuentas",
  "Históricos",
  "Finalizar",
];

/**
 * Map a 7-screen `WizardScreen` into the 4-step stepper indicator.
 */
function screenToStep(screen: WizardScreen): 1 | 2 | 3 | 4 {
  switch (screen) {
    case "step1":
      return 1;
    case "step2_detect":
    case "step2_configure":
      return 2;
    case "step3_upload":
    case "step3_new_accounts":
    case "step3_commit":
      return 3;
    case "finish":
      return 4;
  }
}

/**
 * Orchestrator for the redesigned wizard. Owns:
 *
 *   - persisted state via `useSetupState`
 *   - transient state (detected accounts, pending stashed XML temp ids,
 *     unresolved new accounts from a stash) — frontend-only, NOT in DB
 *   - screen routing via `deriveScreen` (pure, unit-testable)
 *
 * The 7-screen state machine is documented in `useWizardNav.ts`.
 *
 * Step 3 components are placeholders for Task 14, which will swap them for
 * the real upload / new-accounts / commit components.
 */
export function WizardPage() {
  const { state, refetch, isLoading } = useSetupState();

  const [detectedAccounts, setDetectedAccounts] = useState<
    DetectedAccount[] | null
  >(null);
  const [pendingTempIds, setPendingTempIds] = useState<string[]>([]);
  const [unresolvedNewAccounts, setUnresolvedNewAccounts] = useState<
    DetectedAccount[]
  >([]);

  const transient: WizardTransientState = useMemo(
    () => ({
      detectedAccounts,
      pendingTempIds,
      unresolvedNewAccounts,
    }),
    [detectedAccounts, pendingTempIds, unresolvedNewAccounts],
  );

  const screen = useWizardScreen(state, transient);

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Cargando estado…</p>;
  }

  function handleStep1Complete() {
    // Clear any stale detect cache so Step2Detect re-runs cleanly.
    setDetectedAccounts(null);
    refetch();
  }

  function handleDetected(accounts: DetectedAccount[]) {
    setDetectedAccounts(accounts);
  }

  function handleStep2ConfigureComplete() {
    // Clear transient cache so refetch becomes the source of truth.
    setDetectedAccounts(null);
    refetch();
  }

  function handleBackToStep1() {
    // From step2_detect or step2_configure — drop transient and force step1.
    // The backend doesn't expose a "clear step1_credentials" endpoint yet, so
    // the user actually edits in place by submitting step1/save again.
    setDetectedAccounts(null);
    refetch();
  }

  const stepperStep = screenToStep(screen);

  return (
    <div>
      <Stepper current={stepperStep} labels={WIZARD_LABELS} />

      {screen === "step1" && (
        <Step1Credentials onComplete={handleStep1Complete} />
      )}

      {screen === "step2_detect" && (
        <Step2Detect onDetected={handleDetected} onBack={handleBackToStep1} />
      )}

      {screen === "step2_configure" && detectedAccounts && (
        <Step2ConfigureAccounts
          detected={detectedAccounts}
          onComplete={handleStep2ConfigureComplete}
          onBack={() => setDetectedAccounts(null)}
        />
      )}

      {screen === "step3_upload" && (
        <div className="rounded border p-4 text-sm text-muted-foreground">
          [TASK 14 PLACEHOLDER] Step3Upload component goes here. It will
          consume <code>setPendingTempIds</code> and{" "}
          <code>setUnresolvedNewAccounts</code> from WizardPage state.
        </div>
      )}

      {screen === "step3_new_accounts" && (
        <div className="rounded border p-4 text-sm text-muted-foreground">
          [TASK 14 PLACEHOLDER] Step3NewAccountsModal component goes here.
          unresolvedNewAccounts.length = {unresolvedNewAccounts.length}
        </div>
      )}

      {screen === "step3_commit" && (
        <div className="rounded border p-4 text-sm text-muted-foreground">
          [TASK 14 PLACEHOLDER] Step3Commit component goes here.
          pendingTempIds.length = {pendingTempIds.length}
        </div>
      )}

      {screen === "finish" && <StepFinish />}
    </div>
  );
}
