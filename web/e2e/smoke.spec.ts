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
  await expect(card.getByText("Analysis", { exact: true })).toBeVisible();
  await expect(card.getByText(/^Likely cause:/)).toBeVisible();
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

test("the audit explorer shows what left the system, masked", async ({ page }) => {
  await signIn(page, "Meera Nair");
  await go(page, "Audit");
  await page.getByRole("button", { name: /documents\.extract/ }).click();
  await expect(page.getByRole("heading", { name: "What left our system" })).toBeVisible();
  await expect(page.getByText(/PAN: <PAN_1>/)).toBeVisible();
  await page.getByRole("button", { name: "Verify chain" }).click();
  await expect(page.getByText(/Chain intact/)).toBeVisible();
  await page.getByLabel("Effect").selectOption("DENY");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByRole("button", { name: /documents\.extract/ })).toBeHidden();
  await page.getByLabel("Action contains").fill("nothing-matches");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByText("No events match")).toBeVisible();
});

test("employees show a record without document contents", async ({ page }) => {
  await signIn(page, "Meera Nair");
  await go(page, "Employees");
  await page.getByRole("button", { name: "Show record for Aarav Mehta" }).click();
  await expect(page.getByText("Sensitive fields stay encrypted")).toBeVisible();
  await expect(page.getByText("Pre hire created")).toBeVisible();
});

test("the registry lists workflow versions", async ({ page }) => {
  await signIn(page, "Meera Nair");
  await go(page, "Registry");
  await expect(page.getByRole("rowheader", { name: "Onboarding" })).toBeVisible();
});

test("chat answers with a source, and says when it goes to a person", async ({ page }) => {
  await signIn(page, "Meera Nair");
  await go(page, "Ask Relay");
  await page.getByRole("button", { name: "How many leaves do I get in probation?" }).click();
  await expect(page.getByText(/Source: Leave during probation/)).toBeVisible();
  await page.getByLabel("Your message").fill("the coffee machine is broken");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("Sent to a person")).toBeVisible();
});

const PDF = { name: "pan.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.7 synthetic") };

test("a joiner uploads with no account, and consent is explicit", async ({ page }) => {
  await page.goto("/upload?token=one-time");
  const send = page.getByRole("button", { name: "Send my documents" });
  await expect(send).toBeDisabled();
  await page.getByLabel("PAN card").setInputFiles(PDF);
  await expect(send).toBeDisabled(); // nothing is ticked for them
  await page.getByLabel("Verify my identity").check();
  await send.click();
  await expect(page.getByRole("heading", { name: /we have your documents/ })).toBeVisible();
});

test("an oversized file is caught before upload, and a bare link explains itself", async ({ page }) => {
  await page.goto("/upload?token=t");
  await page.getByLabel("PAN card").setInputFiles({ ...PDF, buffer: Buffer.alloc(10 * 1024 * 1024 + 1) });
  await expect(page.getByText(/larger than 10 MB/)).toBeVisible();
  await page.goto("/upload");
  await expect(page.getByRole("heading", { name: "This link is incomplete" })).toBeVisible();
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

test("the dashboard compares workflows", async ({ page }) => {
  await signIn(page, "Meera Nair");
  const table = page.getByRole("table", { name: "By workflow" });
  await expect(table.getByRole("rowheader", { name: "Onboarding" })).toBeVisible();
  await expect(table.getByRole("rowheader", { name: "Exit" })).toBeVisible();
});

test("a person raises a data request and sees it listed", async ({ page }) => {
  await signIn(page, "Meera Nair");
  await go(page, "Data requests");
  await expect(page.getByRole("rowheader", { name: "Neha Gupta" })).toBeVisible();
  await page.getByRole("button", { name: "See my data" }).click();
  await expect(page.getByRole("status")).toContainText("Request raised");
  await expect(page.getByRole("rowheader", { name: "Meera Nair" })).toBeVisible();
  const correct = page.getByRole("button", { name: "Correct my data" });
  await expect(correct).toBeDisabled(); // a correction must say what to fix
  await page.getByLabel(/What should change/).fill("My surname is Singh, not Sing");
  await correct.click();
  await expect(page.getByRole("cell", { name: "Correction" })).toBeVisible();
});

test("erasing your data asks first, and can be called off", async ({ page }) => {
  await signIn(page, "Meera Nair");
  await go(page, "Data requests");
  const erase = page.getByRole("button", { name: "Erase my data" });
  const confirm = page.getByRole("group", { name: "Confirm erasure request" });
  await erase.click();
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await expect(confirm).toBeHidden();
  await expect(page.getByRole("cell", { name: "Erasure" })).toHaveCount(0);
  await erase.click();
  await confirm.getByRole("button", { name: "Send erasure request" }).click();
  await expect(page.getByRole("cell", { name: "Erasure" })).toBeVisible();
});

for (const { who, name, raises, opensRuns } of [
  { who: "HR Ops", name: "Rohan Das", raises: true, opensRuns: true },
  { who: "a manager", name: "Arjun Rao", raises: true, opensRuns: false },
  { who: "an auditor", name: "Leela Iyer", raises: false, opensRuns: true },
  { who: "an approver", name: "Kabir Shah", raises: false, opensRuns: false },
]) {
  test(`the data requests page offers ${who} only what they can use`, async ({ page }) => {
    await signIn(page, name);
    await go(page, "Data requests");
    await expect(page.getByRole("rowheader", { name: "Neha Gupta" })).toBeVisible();
    await expect(page.getByRole("button", { name: "See my data" })).toHaveCount(raises ? 1 : 0);
    await expect(page.getByRole("link", { name: "View run" })).toHaveCount(opensRuns ? 1 : 0);
  });
}

test("linked runs point both ways between onboarding and probation", async ({ page }) => {
  await signIn(page, "Meera Nair");
  await go(page, "Runs");
  await page.getByRole("button", { name: "Onboarding probation" }).click();
  await expect(page).toHaveURL(/workflow=onboarding_probation/);
  await expect(page.getByRole("link", { name: /Diya Kapoor/ })).toBeHidden();
  await page.getByRole("link", { name: /Neha Gupta/ }).click();
  const linked = page.getByRole("heading", { name: "Linked runs" }).locator("..");
  await expect(linked).toContainText("Started by");
  await linked.getByRole("link", { name: "Onboarding", exact: true }).click();
  await expect(page).toHaveURL(/\/runs\/run-0024$/);
  const back = page.getByRole("heading", { name: "Linked runs" }).locator("..");
  await expect(back.getByRole("link", { name: "Onboarding probation" })).toBeVisible();
});

test("a manager accepts a resignation with a release date or a buyout, not both", async ({ page }) => {
  await signIn(page, "Arjun Rao");
  await go(page, "Inbox");
  const card = page.locator("form").filter({
    has: page.getByRole("group", { name: "Options for Accept resignation" }),
  });
  await card.getByRole("button", { name: "Accept", exact: true }).click();
  const release = card.getByLabel("Release date");
  const buyout = card.getByLabel("Buyout of the notice period agreed");
  await release.fill("2026-12-18");
  await expect(buyout).toBeDisabled();
  await release.fill("");
  await expect(buyout).toBeEnabled();
  await buyout.check();
  await expect(release).toBeDisabled();
  await card.getByRole("button", { name: /Record decision/ }).click();
  await expect(page.getByRole("heading", { name: "Accept resignation" })).toBeHidden();
});

test("an employee on notice shows the last day and links to every workflow", async ({ page }) => {
  await signIn(page, "Meera Nair");
  await go(page, "Employees");
  await expect(page.getByText("Last day 2026-12-18")).toBeVisible();
  await page.getByRole("button", { name: "Show record for Kiran Rao" }).click();
  await page.getByRole("link", { name: "Exit", exact: true }).click();
  await expect(page).toHaveURL(/\/runs\/run-0033$/);
  await go(page, "Employees");
  await page.getByRole("button", { name: "Show record for Neha Gupta" }).click();
  await expect(page.getByRole("link", { name: "Onboarding probation" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Onboarding", exact: true })).toBeVisible();
});
