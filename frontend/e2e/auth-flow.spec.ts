import { test, expect } from "@playwright/test";

test("register -> login -> settings update -> logout flow", async ({ page }) => {
  const ts = Date.now();
  const email = `user${ts}@example.com`;
  const password = "supersecret123";

  // Register
  await page.goto("/register");
  await page.getByLabel("Nombre").fill("Test Owner Test");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel(/Contraseña/).fill(password);
  await page.getByRole("button", { name: /Crear cuenta/i }).click();

  // Tras registro va a /login
  await expect(page).toHaveURL(/\/login/);

  // Login
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Contraseña").fill(password);
  await page.getByRole("button", { name: /Entrar/i }).click();
  await expect(page).toHaveURL(/\/dashboard/);

  // Sidebar visible
  await expect(page.getByText("IBKR Control")).toBeVisible();

  // Ir a Settings, cambiar marginal_rate
  await page.getByRole("link", { name: /Settings/i }).click();
  await expect(page).toHaveURL(/\/settings/);
  const rateInput = page.getByLabel(/Tarifa marginal/i);
  await rateInput.fill("0.33");
  await page.getByRole("button", { name: /Guardar/i }).click();
  await expect(page.getByText("Guardado")).toBeVisible();

  // Refrescar y verificar persistencia
  await page.reload();
  await expect(rateInput).toHaveValue("0.3300");

  // Logout
  await page.getByRole("button", { name: /Salir/i }).click();
  await expect(page).toHaveURL(/\/login/);
});
