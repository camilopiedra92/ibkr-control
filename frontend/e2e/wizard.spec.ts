/**
 * E2E tests for the setup wizard happy path.
 *
 * NOTE: These tests assume a backend configured with IBKR mocks.
 * Without mocks, Step 1 will fail at token validation (real IBKR rejects
 * "good-token-e2e"). Run with E2E_BACKEND_MOCKED=1 environment to enable
 * mocks (TODO: implement in backend).
 *
 * The "resume" test is skipped — it requires a backend helper to pre-complete
 * steps 1-3 so we can land on step 4 with a fresh browser session, which
 * needs additional test infrastructure beyond this skeleton.
 */

import { test, expect } from "@playwright/test";
import path from "path";

const TEST_EMAIL = `e2e-wizard-${Date.now()}@test.com`;
const TEST_PASSWORD = "testpassword123";

test.describe("Setup wizard happy path", () => {
  test("register -> /setup -> 4 steps -> /dashboard", async ({ page }) => {
    // NOTE: this test assumes a backend configured with IBKR mocks.
    // Without mocks, Step 1 will fail at token validation (real IBKR rejects
    // "good-token-e2e"). Run with E2E_BACKEND_MOCKED=1 to enable mocks (TODO).

    // ── Step 0: register a fresh user ──────────────────────────────────────
    await page.goto("/register");
    await page.getByLabel("Nombre").fill("E2E Test User");
    await page.getByLabel("Email").fill(TEST_EMAIL);
    await page.getByLabel(/Contraseña/).fill(TEST_PASSWORD);
    await page.getByRole("button", { name: /Crear cuenta/i }).click();

    // After registration -> /login
    await expect(page).toHaveURL(/\/login/, { timeout: 10_000 });

    // ── Log in ─────────────────────────────────────────────────────────────
    await page.getByLabel("Email").fill(TEST_EMAIL);
    await page.getByLabel("Contraseña").fill(TEST_PASSWORD);
    await page.getByRole("button", { name: /Entrar/i }).click();

    // New user with no setup completed -> redirected to /setup
    await expect(page).toHaveURL(/\/setup/, { timeout: 10_000 });

    // ── Step 1: Credenciales IBKR Flex ────────────────────────────────────
    // Stepper heading visible
    await expect(page.getByText("Credenciales")).toBeVisible();

    // Fill token (min 10 chars) and query ID
    await page.getByLabel("Flex Token (read-only)").fill("good-token-e2e-test");
    await page.getByLabel("YTD Flex Query ID").fill("123456");

    // Submit — backend mock should return success
    await page.getByRole("button", { name: /Continuar/i }).click();

    // ── Step 2: Cuentas IBKR + Participaciones ────────────────────────────
    await expect(page.getByText("Cuentas IBKR")).toBeVisible({ timeout: 10_000 });

    // Default accounts are pre-filled; accept them
    await page.getByRole("button", { name: /Continuar/i }).click();

    // ── Step 3: XMLs historicos (opcional) ────────────────────────────────
    await expect(page.getByText("XMLs historicos")).toBeVisible({ timeout: 10_000 });

    // Upload the E2E fixture via the hidden file input inside react-dropzone
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(
      path.join(__dirname, "fixtures", "test_xml.xml")
    );

    // Wait for upload to complete (status icon changes from pending to ok/duplicate)
    await expect(
      page.locator("li").filter({ hasText: "test_xml.xml" })
    ).toBeVisible({ timeout: 15_000 });

    // Skip upload is fine; just click Continuar
    await page.getByRole("button", { name: /Continuar/i }).click();

    // ── Step 4: Descarga inicial de datos ─────────────────────────────────
    await expect(page.getByText("Descarga inicial de datos")).toBeVisible({
      timeout: 10_000,
    });

    // The job starts automatically; wait for completion message or dashboard redirect
    // (with mocked backend the job finishes quickly)
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 60_000 });
  });

  test.skip("resume wizard from step 2 after browser restart", async ({
    page: _page,
  }) => {
    // TODO: implement when backend has a test helper to pre-complete wizard
    // steps so we can land on step 2 with a fresh browser context.
    // The key behavior to test: useSetupState() reads /api/setup/status, sees
    // step1_completed_at set but step2_completed_at null, and renders Step2.
  });
});
