/**
 * E2E test: wizard redesign — happy path.
 *
 * step1 (save credentials) → step2 detect → step2 configure → skip step3 → finish
 *
 * All `/api/*` calls are mocked via `page.route`. The auth token is injected
 * into localStorage with `page.addInitScript` so the AuthGate component lets
 * us into `/setup` without hitting a real backend.
 *
 * Selector strategy: prefer real UI strings (Spanish, as they appear in the
 * implemented components) over the plan's draft strings.
 *
 * CORS: the frontend is configured with NEXT_PUBLIC_API_URL=http://localhost:8000
 * and runs on http://localhost:3000, so every `/api/*` request is cross-origin
 * and triggers a CORS preflight. All `route.fulfill` responses include the
 * required Access-Control-Allow-* headers so the browser does not block.
 *
 * Requires the Next.js dev server on http://localhost:3000 (no `webServer`
 * is configured in playwright.config.ts).
 */
import { test, expect, Route } from "@playwright/test";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "http://localhost:3000",
  "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
  "Access-Control-Allow-Headers":
    "Content-Type, Authorization, X-Requested-With",
  "Access-Control-Allow-Credentials": "true",
};

/**
 * Helper: respond to a request with JSON + the CORS headers required for the
 * browser to actually surface the response to the page script. Also handles
 * OPTIONS preflight cleanly.
 */
async function fulfillJson(
  route: Route,
  body: unknown,
  status = 200,
): Promise<void> {
  if (route.request().method() === "OPTIONS") {
    await route.fulfill({ status: 204, headers: CORS_HEADERS });
    return;
  }
  await route.fulfill({
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

const STATE_INITIAL = {
  step1_credentials: false,
  step2_accounts: false,
  step3_xmls: false,
  step3_n_xmls_uploaded: 0,
  setup_completed_at: null,
  pending_stash_temp_ids: [],
};

const STATE_AFTER_STEP1 = {
  ...STATE_INITIAL,
  step1_credentials: true,
};

const STATE_AFTER_STEP2 = {
  ...STATE_AFTER_STEP1,
  step2_accounts: true,
};

const STATE_FINISHED = {
  ...STATE_AFTER_STEP2,
  step3_xmls: true,
  setup_completed_at: "2026-05-24T10:00:00Z",
};

test("wizard happy path: step1 → step2 detect → configure → skip step3 → finish", async ({
  page,
}) => {
  let currentState: object = STATE_INITIAL;

  await page.addInitScript(() => {
    window.localStorage.setItem("auth_token", "e2e-mock-token");
  });

  // Playwright route precedence: the most recently added matching route wins.
  // Register the catch-all FIRST so the specific routes below override it for
  // their patterns.
  await page.route("**/api/**", async (route) => {
    await fulfillJson(route, { ok: true });
  });

  await page.route("**/api/setup/state", async (route) => {
    await fulfillJson(route, currentState);
  });

  await page.route("**/api/setup/step1/save", async (route) => {
    currentState = STATE_AFTER_STEP1;
    await fulfillJson(route, { ok: true });
  });

  await page.route("**/api/setup/step2/detect", async (route) => {
    await fulfillJson(route, {
      detected_accounts: [
        {
          ibkr_account_id: "U99999999",
          suggested_alias: "Test Account",
          account_type: "Individual",
          account_holder: "E2E Test User",
        },
      ],
    });
  });

  await page.route("**/api/setup/step2/save", async (route) => {
    currentState = STATE_AFTER_STEP2;
    await fulfillJson(route, { ok: true });
  });

  await page.route("**/api/setup/step3/commit", async (route) => {
    currentState = STATE_FINISHED;
    await fulfillJson(route, {
      flex_import_ids: [],
      total_rows_inserted: 0,
    });
  });

  await page.goto("/setup");
  await expect(page.getByText("Credenciales IBKR Flex")).toBeVisible({
    timeout: 10_000,
  });

  await page.locator("input#token").fill("tok_test_1234567890");
  await page.locator("input#queryId").fill("999");
  await page.getByRole("button", { name: /Continuar/i }).click();

  // step2_detect renders briefly (auto-triggers detect on mount) before
  // transitioning to step2_configure as soon as the mocked detect resolves.
  // With instant mocks we may not catch the transient detect heading, so
  // assert directly on the configure screen.
  await expect(page.getByText("Configurar cuentas detectadas")).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.locator("text=U99999999")).toBeVisible();

  await page.getByRole("button", { name: /Continuar/i }).click();

  await expect(page.getByText("XMLs históricos (opcional)")).toBeVisible({
    timeout: 10_000,
  });

  await page.getByRole("button", { name: /Saltar/i }).click();

  await expect(page.getByText("Setup completado")).toBeVisible({
    timeout: 10_000,
  });
});
