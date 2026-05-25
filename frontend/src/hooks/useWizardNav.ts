import { useMemo } from "react";
import type { WizardStateResponse, DetectedAccount } from "@/lib/api";

/**
 * Wizard screen identifiers — 7 screens total covering the redesigned flow.
 *
 *  - step1            credentials form (Flex token + YTD query id)
 *  - step2_detect     auto-call /step2/detect (loading + retry visualization)
 *  - step2_configure  show detected accounts, edit alias + pct, submit /step2/save
 *  - step3_upload     drop-zone for historical XMLs (one-by-one)
 *  - step3_new_accounts  modal asking pct for accounts that appear only in XML
 *  - step3_commit     "commit N stashed XMLs" confirmation step
 *  - finish           dashboard handoff
 */
export type WizardScreen =
  | "step1"
  | "step2_detect"
  | "step2_configure"
  | "step3_upload"
  | "step3_new_accounts"
  | "step3_commit"
  | "finish";

/**
 * Frontend-only ephemeral state that is NOT persisted on the backend but is
 * needed to disambiguate between screens that share the same persisted state.
 *
 * Example: after /step2/detect returns successfully but before the user hits
 * "Save accounts", the backend `step2_accounts` flag is still false; we use
 * `detectedAccounts != null` to know we should render step2_configure.
 */
export interface WizardTransientState {
  detectedAccounts: DetectedAccount[] | null;
  pendingTempIds: string[];
  unresolvedNewAccounts: DetectedAccount[];
}

/**
 * Pure transition function: (persisted state, transient state) -> screen.
 *
 * Kept side-effect free so it can be unit-tested without React.
 */
export function deriveScreen(
  state: WizardStateResponse | undefined,
  transient: WizardTransientState,
): WizardScreen {
  if (!state) return "step1";
  if (state.setup_completed_at) return "finish";
  if (!state.step1_credentials) return "step1";
  if (!state.step2_accounts) {
    return transient.detectedAccounts ? "step2_configure" : "step2_detect";
  }
  if (!state.step3_xmls) {
    if (transient.unresolvedNewAccounts.length > 0) return "step3_new_accounts";
    if (transient.pendingTempIds.length > 0) return "step3_commit";
    return "step3_upload";
  }
  return "finish";
}

/**
 * React hook wrapper around `deriveScreen` with memoization.
 */
export function useWizardScreen(
  state: WizardStateResponse | undefined,
  transient: WizardTransientState,
): WizardScreen {
  return useMemo(() => deriveScreen(state, transient), [state, transient]);
}
