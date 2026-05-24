import { defineConfig } from "orval";

export default defineConfig({
  api: {
    input: "./openapi.json",
    output: {
      target: "./src/lib/api/generated.ts",
      client: "react-query",
      httpClient: "axios",
      mode: "single",
      override: {
        mutator: {
          path: "./src/lib/api/mutator.ts",
          name: "axiosMutator",
        },
        query: {
          useQuery: true,
          useMutation: true,
        },
      },
    },
  },
});
