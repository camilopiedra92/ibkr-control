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
