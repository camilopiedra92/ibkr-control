/**
 * E2E test: wizard step3 — uploading an XML that contains a previously-unseen
 * account triggers the "Cuentas nuevas detectadas" modal asking for alias + %.
 *
 * State has step1 + step2 complete → screen = step3_upload.
 * User picks an XML; POST /step3/upload returns the file's new_accounts.
 * User clicks "Continuar con 1 XML →".
 * WizardPage advances to step3_new_accounts and the modal heading
 * "Cuentas nuevas detectadas" is visible.
 *
 * CORS: see wizard-happy-path.spec.ts comment block.
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

test("step3 upload reveals new accounts, modal asks for alias+pct", async ({
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
      step2_accounts: true,
      step3_xmls: false,
      step3_n_xmls_uploaded: 0,
      setup_completed_at: null,
      pending_stash_temp_ids: [],
    });
  });

  await page.route("**/api/setup/step3/upload", async (route) => {
    await fulfillJson(route, {
      flex_import_temp_id: "abc-temp-id",
      detected_accounts: [
        {
          ibkr_account_id: "U99999999",
          suggested_alias: "x",
          account_type: "Individual",
          account_holder: "X",
        },
      ],
      new_accounts: [
        {
          ibkr_account_id: "U99999999",
          suggested_alias: "x",
          account_type: "Individual",
          account_holder: "X",
        },
      ],
      period: { from: "2024-01-01", to: "2024-12-31" },
      anyo: 2024,
      sha256: "abc",
    });
  });

  await page.goto("/setup");

  await expect(page.getByText("XMLs históricos (opcional)")).toBeVisible({
    timeout: 10_000,
  });

  const fileInput = page.locator('input[type="file"]');
  await fileInput.setInputFiles({
    name: "hist-2024.xml",
    mimeType: "application/xml",
    buffer: Buffer.from("<FlexQueryResponse/>"),
  });

  await expect(
    page.getByRole("button", { name: /Continuar con 1 XML/i }),
  ).toBeEnabled({ timeout: 10_000 });
  await page.getByRole("button", { name: /Continuar con 1 XML/i }).click();

  await expect(page.getByText("Cuentas nuevas detectadas")).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.locator("text=U99999999")).toBeVisible();
});
