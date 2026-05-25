/**
 * E2E test: wizard step2 — IBKR_BUSY 503 surfaces the "Subir XML manualmente"
 * fallback affordance.
 *
 * State has step1 complete + step2 incomplete → screen = step2_detect.
 * Auto-triggered POST /step2/detect returns 503 with code IBKR_BUSY.
 * The error block renders the Spanish copy "IBKR está ocupado" + the
 * "Subir XML manualmente" button.
 *
 * CORS: see wizard-happy-path.spec.ts comment block — all `route.fulfill`
 * responses include CORS headers because the API lives on a different origin
 * than the page (localhost:8000 vs localhost:3000).
 */
import { test, expect, Route } from "@playwright/test";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "http://localhost:3000",
  "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
  "Access-Control-Allow-Headers":
    "Content-Type, Authorization, X-Requested-With",
  "Access-Control-Allow-Credentials": "true",
};

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

test("wizard step2 IBKR_BUSY surfaces error message + 'Subir XML manualmente' fallback", async ({
  page,
}) => {
  await page.addInitScript(() => {
    window.localStorage.setItem("auth_token", "e2e-mock-token");
  });

  // Playwright route precedence: most recently added matching route wins.
  // Catch-all FIRST so specific routes below override it.
  await page.route("**/api/**", async (route) => {
    await fulfillJson(route, { ok: true });
  });

  await page.route("**/api/setup/state", async (route) => {
    await fulfillJson(route, {
      step1_credentials: true,
      step2_accounts: false,
      step3_xmls: false,
      step3_n_xmls_uploaded: 0,
      setup_completed_at: null,
      pending_stash_temp_ids: [],
    });
  });

  await page.route("**/api/setup/step2/detect", async (route) => {
    await fulfillJson(
      route,
      { detail: { code: "IBKR_BUSY", attempts: 4 } },
      503,
    );
  });

  await page.goto("/setup");

  await expect(page.getByText("Detectando tus cuentas IBKR")).toBeVisible({
    timeout: 10_000,
  });

  // After the immediate 503, the typed-error block renders.
  await expect(page.getByText(/IBKR está ocupado/)).toBeVisible({
    timeout: 10_000,
  });

  await expect(
    page.getByRole("button", { name: /Subir XML manualmente/i }),
  ).toBeVisible();
});
