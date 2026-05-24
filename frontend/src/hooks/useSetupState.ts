import { useQuery } from "@tanstack/react-query";
import { axiosInstance } from "@/lib/api";
import type { SetupState as ApiSetupState } from "@/lib/api";

// Re-export the type from generated client for convenience
export type { SetupState as ApiSetupState } from "@/lib/api";

export interface SetupStateResult {
  state: ApiSetupState | undefined;
  isLoading: boolean;
  refetch: () => void;
}

export function useSetupState(): SetupStateResult {
  const query = useQuery<ApiSetupState>({
    queryKey: ["setup-state"],
    queryFn: async () => {
      const r = await axiosInstance.get<ApiSetupState>("/setup/state");
      return r.data;
    },
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
 * 4 → backfill inicial
 */
export function currentStep(state: ApiSetupState | undefined): 1 | 2 | 3 | 4 {
  if (!state) return 1;
  if (!state.step1_credentials) return 1;
  if (!state.step2_accounts) return 2;
  if (!state.step3_xmls) return 3;
  return 4;
}
