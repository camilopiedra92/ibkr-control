/**
 * E2E tests for the Settings page manual refresh button.
 *
 * NOTE: These tests assume:
 * 1. A backend configured with IBKR mocks (E2E_BACKEND_MOCKED=1 — TODO).
 * 2. A pre-completed setup wizard (step1-4 done) so /settings is accessible.
 *
 * Without these, the test will fail because:
 * - POST /api/ingest/trigger requires setup_completed_at to be set.
 * - Real IBKR Flex calls time out or return auth errors.
 */

import { test, expect } from "@playwright/test";

const TEST_EMAIL = `e2e-refresh-${Date.now()}@test.com`;
const TEST_PASSWORD = "testpassword123";

/** Helper: register + log in, redirect to /setup, then complete wizard via API bypass. */
async function registerAndSetupUser(
  page: import("@playwright/test").Page
): Promise<void> {
  // Register
  await page.goto("/register");
  await page.getByLabel("Nombre").fill("E2E Refresh User");
  await page.getByLabel("Email").fill(TEST_EMAIL);
  await page.getByLabel(/Contraseña/).fill(TEST_PASSWORD);
  await page.getByRole("button", { name: /Crear cuenta/i }).click();
  await expect(page).toHaveURL(/\/login/, { timeout: 10_000 });

  // Login
  await page.getByLabel("Email").fill(TEST_EMAIL);
  await page.getByLabel("Contraseña").fill(TEST_PASSWORD);
  await page.getByRole("button", { name: /Entrar/i }).click();
  // After login, new user lands on /setup — we don't run the full wizard here.
  // This test is meaningful only when backend has mock support + a setup-bypass endpoint.
  // For now, navigate directly to /settings (will redirect to /setup if not complete).
}

test.describe("Settings page — manual refresh", () => {
  test("clicking Ejecutar ahora triggers ingest and shows progress", async ({
    page,
  }) => {
    // NOTE: requires backend with IBKR mocks (E2E_BACKEND_MOCKED=1 — TODO)
    // and a pre-completed wizard. Without it the POST /api/ingest/trigger will
    // return 400 "Setup not completed".

    await registerAndSetupUser(page);

    // Navigate to settings (requires auth + completed setup in a mocked env)
    await page.goto("/settings");

    // The "Refresh manual" section should be visible
    await expect(page.getByText("Refresh manual")).toBeVisible({
      timeout: 10_000,
    });

    // Click "Ejecutar ahora"
    await page.getByRole("button", { name: /Ejecutar ahora/i }).click();

    // Button changes to "Ejecutando…" while running
    await expect(
      page.getByRole("button", { name: /Ejecutando/i })
    ).toBeVisible({ timeout: 5_000 });

    // Sub-step "TRM (DIAN)" appears in the progress list
    await expect(page.getByText("TRM (DIAN)")).toBeVisible({ timeout: 5_000 });

    // Sub-step "Flex YTD (IBKR)" appears
    await expect(page.getByText("Flex YTD (IBKR)")).toBeVisible({
      timeout: 5_000,
    });

    // Wait for the success message (mocked backend finishes quickly)
    await expect(
      page.getByText("Refresh completado correctamente")
    ).toBeVisible({ timeout: 60_000 });
  });
});
