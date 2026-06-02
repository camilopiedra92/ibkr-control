import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import tseslint from "typescript-eslint";

// eslint-config-next/core-web-vitals already registers @typescript-eslint
// (plugin + parser, non-type-checked recommended). Re-spreading
// tseslint.configs.recommendedTypeChecked would register the plugin twice
// (a flat-config error), so we extract just its rules and layer them on top,
// reusing the already-registered plugin, plus enable the type-aware parser.
const typeCheckedRules = Object.assign(
  {},
  ...tseslint.configs.recommendedTypeChecked.map((c) => c.rules ?? {}),
);

const config = [
  {
    ignores: [
      "src/lib/api/generated.ts", // orval-generated client — not hand-written
      ".next/**",
      "node_modules/**",
      "next-env.d.ts",
      "playwright-report/**",
      "test-results/**",
      "coverage/**",
    ],
  },
  ...nextCoreWebVitals,
  {
    files: ["**/*.{ts,tsx,mts,cts}"],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      ...typeCheckedRules,
      // Intentionally-unused args/vars are prefixed with `_`.
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      // Passing an async handler to a JSX event attribute (onClick/onSubmit) is
      // idiomatic in React — the return value is ignored. We still flag misused
      // promises everywhere else (object properties, etc.).
      "@typescript-eslint/no-misused-promises": [
        "error",
        { checksVoidReturn: { attributes: false } },
      ],
    },
  },
];

export default config;
