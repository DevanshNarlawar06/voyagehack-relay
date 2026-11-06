import { expect, test, type Page } from "@playwright/test";

// The WP10a acceptance journey (PRD 21): login, dashboard, resolve a task, activate a policy, kill switch.

async function signIn(page: Page, name: string) {
  await page.goto("/login");
  await page.getByRole("button", { name: `Sign in as ${name}` }).click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
}

// Fixture state lives in the page, so the journey moves with in-app links rather than reloads.
const go = (page: Page, label: string) =>
  page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: label }).click();

test("signed-out visitors are sent to login", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
});

test("the full governance journey", async ({ page }) => {
  await signIn(page, "Meera Nair");
  await expect(page.getByText("Step automation")).toBeVisible();
  await expect(page.getByText("Runs by status")).toBeVisible();
  await expect(page.getByRole("note")).toContainText("Demo data");

  // Resolve a conflict; going against the suggestion requires a reason.
  await go(page, "Inbox");
  await expect(page.getByRole("heading", { name: "PAN name does not match the offer" })).toBeVisible();
  const card = page.getByRole("region", { name: "Conflicts" });
  await card.getByRole("button", { name: "Request new document" }).click();
  await expect(card.getByRole("button", { name: /Record decision/ })).toBeDisabled();
  await card.getByLabel("Reason").fill("The scan is unreadable");
  await card.getByRole("button", { name: /Record decision/ }).click();
  await expect(page.getByRole("heading", { name: "PAN name does not match the offer" })).toBeHidden();

  // Activate a policy: blocked until a change passes its tests.
  await go(page, "Policies");
  const editor = page.getByLabel("Policy YAML");
  await expect(editor).toHaveValue(/version: 1/);
  const activate = page.getByRole("button", { name: "Activate" });
  await expect(activate).toBeDisabled();
  const yaml = await editor.inputValue();
  await editor.fill(yaml.replace("version: 1", "version: 2").replace("150000", "100000"));
  await expect(page.getByText("Changes from active v1")).toBeVisible();
  await page.getByRole("button", { name: "Run tests" }).click();
  await expect(page.getByText("3 of 3 passed")).toBeVisible();
  await activate.click();
  await expect(page.getByRole("status")).toContainText("Version 2 is active");

  // Kill switch on and off.
  await go(page, "Controls");
  await page.getByRole("button", { name: "Pause all runs" }).click();
  await expect(page.getByText("Global: *")).toBeVisible();
  await page.getByRole("button", { name: "Release all runs" }).click();
  await expect(page.getByText("No kill switches are on.")).toBeVisible();
});

test("a failing policy test blocks activation", async ({ page }) => {
  await signIn(page, "Meera Nair");
  await go(page, "Policies");
  const editor = page.getByLabel("Policy YAML");
  await expect(editor).toHaveValue(/version: 1/);
  await editor.fill((await editor.inputValue()).replace("150000", "100000"));
  await page.getByRole("button", { name: "Run tests" }).click();
  await expect(page.getByText("2 of 3 passed")).toBeVisible();
  await expect(page.getByRole("button", { name: "Activate" })).toBeDisabled();
});

test("run detail draws the graph and explains a step", async ({ page }) => {
  await signIn(page, "Meera Nair");
  await go(page, "Runs");
  await page.getByRole("link", { name: /Diya Kapoor/ }).click();
  const node = page.locator('button[data-status="WAITING_HUMAN"]');
  await expect(node).toContainText("Order laptop");
  await expect(node).toBeInViewport();
  await expect(page.getByText("laptop_high_spend").first()).toBeVisible();
  await page.getByRole("button", { name: /Compute terms/ }).click();
  await expect(page.getByRole("heading", { name: "Compute terms" })).toBeVisible();
});

test("roles limit what a user can do", async ({ page }) => {
  await signIn(page, "Leela Iyer");
  await expect(page.getByText("Auditor", { exact: true })).toBeVisible();
  await go(page, "Controls");
  await expect(page.getByRole("button", { name: "Pause all runs" })).toBeDisabled();
  await go(page, "Inbox");
  await expect(page.getByText(/Only someone with the/).first()).toBeVisible();
  await expect(page.getByRole("button", { name: /Record decision/ })).toHaveCount(0);
});
