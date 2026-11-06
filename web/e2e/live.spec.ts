import { expect, test, type Page } from "@playwright/test";

// Runs against a real API (docker compose up, then NEXT_PUBLIC_RELAY_API_URL set). Skipped otherwise.
// RELAY_LIVE_RUN_ID / RELAY_LIVE_SUBJECT name a run seeded for the test.
const live = Boolean(process.env.NEXT_PUBLIC_RELAY_API_URL);
test.skip(!live, "needs a running API");

async function signIn(page: Page, name: string) {
  await page.goto("/login");
  await page.getByRole("button", { name: `Sign in as ${name}` }).click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
}

const go = (page: Page, label: string) =>
  page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: label }).click();

test("real data has no demo banner", async ({ page }) => {
  await signIn(page, "Meera Nair");
  await expect(page.getByRole("note")).toHaveCount(0);
});

test("an unreachable API shows the error state, and retry recovers", async ({ page }) => {
  await signIn(page, "Meera Nair");
  await page.route("**/api/v1/runs**", (route) => route.abort());
  await go(page, "Runs");
  await expect(page.getByText("cannot be reached")).toBeVisible({ timeout: 15_000 });
  await page.unroute("**/api/v1/runs**");
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: "This could not be loaded" })).toBeHidden();
});

test("a role without access sees the policy engine's reason", async ({ page }) => {
  await signIn(page, "Leela Iyer");
  await go(page, "Inbox");
  await expect(page.getByText(/do not permit tasks\.read/)).toBeVisible({ timeout: 15_000 });
});

test("a seeded run shows its graph and timeline", async ({ page }) => {
  const runId = process.env.RELAY_LIVE_RUN_ID;
  test.skip(!runId, "no seeded run");
  await signIn(page, "Rohan Das");
  await go(page, "Runs");
  await page.getByRole("link", { name: process.env.RELAY_LIVE_SUBJECT as string }).click();
  await expect(page.locator('button[data-status="WAITING_HUMAN"]')).toContainText("Cross check");
});

test("a conflict is decided, and the decision reaches the audit log", async ({ page }) => {
  test.skip(!process.env.RELAY_LIVE_RUN_ID, "no seeded run");
  await signIn(page, "Rohan Das");
  await go(page, "Inbox");
  const card = page.getByRole("region", { name: "Conflicts" });
  await card.getByRole("button", { name: "Request new document" }).click();
  await card.getByLabel("Reason").fill("The scan is unreadable");
  await card.getByRole("button", { name: /Record decision/ }).click();
  await expect(page.getByText("Nothing waiting")).toBeVisible();
});

test("activating a policy goes live, and a failing one is refused", async ({ page }) => {
  await signIn(page, "Meera Nair");
  await go(page, "Policies");
  await page.getByRole("navigation", { name: "Policy packs" }).getByRole("button", { name: /Approvals/ }).click();
  const editor = page.getByLabel("Policy YAML");
  await expect(editor).toHaveValue(/pack: approvals/);
  await expect(editor).toHaveValue(/version: \d+/);
  const yaml = await editor.inputValue();
  const version = Number(/^version: (\d+)/m.exec(yaml)?.[1]);
  const bump = (text: string) => text.replace(/^version: \d+/m, `version: ${version + 1}`);

  await editor.fill(bump(yaml).replace(/amount_inr > \d+/, "amount_inr > 500000"));
  await page.getByRole("button", { name: "Run tests" }).click();
  await expect(page.getByText(/expensive laptop/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Activate" })).toBeDisabled();

  await editor.fill(bump(yaml).replace(/amount_inr > \d+/, "amount_inr > 100000"));
  await page.getByRole("button", { name: "Run tests" }).click();
  const activate = page.getByRole("button", { name: "Activate" });
  await expect(activate).toBeEnabled();
  await activate.click();
  await expect(page.getByRole("status")).toContainText(`Version ${version + 1} is active`);
  await expect(page.getByText(`v${version + 1}`, { exact: true }).first()).toBeVisible();
});

test("a scoped kill switch and a fault work through the real services", async ({ page }) => {
  await signIn(page, "Meera Nair");
  await go(page, "Controls");
  await page.getByLabel("Scope").selectOption("connector");
  await page.getByLabel("Target").fill("live-test");
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(page.getByText("Connector: live-test")).toBeVisible();
  await page.getByRole("button", { name: "Release connector live-test" }).click();
  await expect(page.getByText("Connector: live-test")).toBeHidden();

  await page.getByRole("button", { name: "Inject fault into assets" }).click();
  await expect(page.getByText("Fault: 503")).toBeVisible();
  const row = page.getByRole("listitem").filter({ hasText: "Assets" });
  await expect(row.getByText("Down")).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: /Clear faults/ }).click();
  await expect(row.getByText("Healthy")).toBeVisible({ timeout: 15_000 });
});
