import { useQuery } from "@tanstack/react-query";
import { getStateApiSetupStateGet } from "@/lib/api";
import type { WizardStateResponse } from "@/lib/api";

// Re-export the type from generated client for convenience
export type { WizardStateResponse } from "@/lib/api";

export interface SetupStateResult {
  state: WizardStateResponse | undefined;
  isLoading: boolean;
  refetch: () => void;
}

export function useSetupState(): SetupStateResult {
  const query = useQuery<WizardStateResponse>({
    queryKey: ["setup-state"],
    queryFn: () => getStateApiSetupStateGet(),
    refetchOnWindowFocus: false,
    retry: false,
  });

  return {
    state: query.data,
    isLoading: query.isLoading,
    refetch: query.refetch,
  };
}

/**
 * Retorna el step actual del wizard basado en el estado persistido.
 * 1 → credenciales Flex pendientes
 * 2 → cuentas pendientes
 * 3 → XMLs históricos (opcional)
 * 4 → backfill inicial / finish
 *
 * NOTE: Legacy compat helper retained so existing setup/page.tsx still
 * type-checks while we land Task 11 in isolation. Tasks 13-14 rewrite
 * the wizard call sites against the new state shape and will remove
 * this helper.
 */
export function currentStep(
  state: WizardStateResponse | undefined,
): 1 | 2 | 3 | 4 {
  if (!state) return 1;
  if (!state.step1_credentials) return 1;
  if (!state.step2_accounts) return 2;
  if (!state.step3_xmls) return 3;
  return 4;
}
