import { test, expect } from "@playwright/test";

/**
 * W1 smoke: the Settings page renders the "Conexiones IBKR" section
 * (replaces the old Flex credentials UI). A fresh user has no connections,
 * so the section shows its empty state + the "Agregar conexión IBKR" form.
 *
 * Mirrors the cheap settings-section pattern in health-banner.spec.ts
 * (register -> login -> /settings -> assert heading visible). No backend
 * mocks required: the section renders client-side regardless of data.
 */
test.describe("Settings page — Conexiones IBKR", () => {
  test("settings page contains Conexiones IBKR section", async ({ page }) => {
    const ts = Date.now();
    const email = `connections-settings-${ts}@example.com`;
    const password = "supersecret123";

    await page.goto("/register");
    await page.getByLabel("Nombre").fill("Connections Settings Test");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel(/Contraseña/).fill(password);
    await page.getByRole("button", { name: /Crear cuenta/i }).click();
    await expect(page).toHaveURL(/\/login/);

    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Contraseña").fill(password);
    await page.getByRole("button", { name: /Entrar/i }).click();
    await expect(page).toHaveURL(/\/dashboard/);

    // Go to Settings
    await page.getByRole("link", { name: /Settings/i }).click();
    await expect(page).toHaveURL(/\/settings/);

    // The "Conexiones IBKR" section heading must be present (W1).
    await expect(
      page.getByRole("heading", { name: /Conexiones IBKR/i })
    ).toBeVisible();

    // A fresh user has no connections: the "Agregar conexión IBKR" form is shown.
    await expect(
      page.getByRole("heading", { name: /Agregar conexión IBKR/i })
    ).toBeVisible();
  });
});
