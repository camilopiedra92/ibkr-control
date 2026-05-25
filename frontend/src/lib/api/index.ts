export * from "./generated";
export { axiosInstance, axiosMutator } from "./mutator";
export { queryClient } from "./queryClient";

// =============================================================================
// LEGACY COMPAT — remove in Tasks 13/14 (wizard redesign)
// -----------------------------------------------------------------------------
// The wizard redesign (commit 37e5681) replaced the Phase 2 setup router with a
// new 9-endpoint flow (step1/save, step2/detect, step2/save, step3/upload,
// step3/save_new_accounts, step3/commit, finish, state, plus
// step2/detect_from_xml). The old endpoints below were removed from the
// backend, so the Orval regen no longer emits them. Tasks 13 and 14 rewrite
// Step1Credentials / Step3Xmls / Step4Initial against the new flow and will
// delete these shims. Until then, leaving non-functional stubs lets the build
// pass so Task 11 can land standalone (per plan §Task 11 step 6).
// =============================================================================

const _wizardRedesignStub =
  (name: string) =>
  (..._args: unknown[]): Promise<never> => {
    throw new Error(
      `${name} was removed by the wizard redesign. ` +
        "Components using it are scheduled for rewrite in Tasks 13/14. " +
        "If you hit this at runtime, the wizard rewrite did not finish.",
    );
  };

export const step1ValidateApiSetupStep1ValidatePost = _wizardRedesignStub(
  "step1ValidateApiSetupStep1ValidatePost",
);
export const step3CompleteApiSetupStep3CompletePost = _wizardRedesignStub(
  "step3CompleteApiSetupStep3CompletePost",
);
export const step4StartApiSetupStep4StartPost = _wizardRedesignStub(
  "step4StartApiSetupStep4StartPost",
);
