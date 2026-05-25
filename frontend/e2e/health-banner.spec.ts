import { test, expect } from "@playwright/test";

test.describe("Ingest health visibility", () => {
  test("fresh user: dashboard renders with no banner", async ({ page }) => {
    const ts = Date.now();
    const email = `health-banner-${ts}@example.com`;
    const password = "supersecret123";

    // Register
    await page.goto("/register");
    await page.getByLabel("Nombre").fill("Health Banner Test");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel(/Contraseña/).fill(password);
    await page.getByRole("button", { name: /Crear cuenta/i }).click();
    await expect(page).toHaveURL(/\/login/);

    // Login
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Contraseña").fill(password);
    await page.getByRole("button", { name: /Entrar/i }).click();
    await expect(page).toHaveURL(/\/dashboard/);

    // No banner: a fresh user has no ingest_log rows, severity = "ok", banner hidden.
    // Banner uses role="alert" + data-severity attribute when shown.
    await expect(page.locator('[role="alert"][data-severity]')).toHaveCount(0);
  });

  test("settings page contains Salud de ingesta section", async ({ page }) => {
    const ts = Date.now();
    const email = `health-settings-${ts}@example.com`;
    const password = "supersecret123";

    await page.goto("/register");
    await page.getByLabel("Nombre").fill("Health Settings Test");
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

    // The "Salud de ingesta" section heading must be present
    await expect(
      page.getByRole("heading", { name: /Salud de ingesta/i })
    ).toBeVisible();

    // The section must have id="salud-ingesta" (for deep-link from banner)
    await expect(page.locator("section#salud-ingesta")).toBeVisible();
  });

  test("deep-link #salud-ingesta scrolls to the section", async ({ page }) => {
    const ts = Date.now();
    const email = `health-deep-${ts}@example.com`;
    const password = "supersecret123";

    await page.goto("/register");
    await page.getByLabel("Nombre").fill("Deep Link Test");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel(/Contraseña/).fill(password);
    await page.getByRole("button", { name: /Crear cuenta/i }).click();
    await expect(page).toHaveURL(/\/login/);

    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Contraseña").fill(password);
    await page.getByRole("button", { name: /Entrar/i }).click();
    await expect(page).toHaveURL(/\/dashboard/);

    // Navigate to /settings#salud-ingesta directly (matches banner href when
    // the banner is visible). The URL fragment is what browsers use to
    // auto-scroll; the banner now uses "#salud-ingesta" (not "?tab=").
    await page.goto("/settings#salud-ingesta");

    // The Salud de ingesta section is visible (rendered, in viewport after anchor jump).
    const section = page.locator("section#salud-ingesta");
    await expect(section).toBeVisible();
    await expect(
      section.getByRole("heading", { name: /Salud de ingesta/i })
    ).toBeVisible();
  });
});
