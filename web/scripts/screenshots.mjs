// Capture README screenshots of the console (fixtures mode) with the installed Chrome.
// Usage: node scripts/screenshots.mjs [baseUrl] [outDir]
import { mkdirSync } from "node:fs";
import { chromium } from "@playwright/test";

const base = process.argv[2] ?? "https://relay-hr-console.vercel.app";
const out = process.argv[3] ?? "../docs/images";
mkdirSync(out, { recursive: true });

const browser = await chromium.launch({ channel: "chrome" });
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
  colorScheme: "light",
});
const page = await context.newPage();

async function shot(name, full = false) {
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: full });
  console.log("saved", name);
}

const nav = (label) =>
  page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: label }).click();

await page.goto(`${base}/welcome`);
await shot("01-welcome");

await page.goto(`${base}/login`);
await page.getByRole("button", { name: "Sign in as Meera Nair" }).waitFor();
await shot("02-sign-in");

await page.getByRole("button", { name: "Sign in as Meera Nair" }).click();
await page.getByRole("heading", { name: "Dashboard" }).waitFor();
await shot("03-dashboard", true);

await nav("Runs");
await page.getByRole("link", { name: /Diya Kapoor/ }).waitFor();
await shot("04-runs");

await page.getByRole("link", { name: /Diya Kapoor/ }).click();
await page.locator('button[data-status="WAITING_HUMAN"]').waitFor();
await page.getByRole("button", { name: /Order laptop/ }).first().click().catch(() => {});
await shot("05-run-detail", true);

await nav("Inbox");
await page.getByRole("heading", { name: "PAN name does not match the offer" }).waitFor();
await page.getByRole("heading", { name: "PAN name does not match the offer" }).scrollIntoViewIfNeeded();
await shot("06-inbox");

await nav("Policies");
const editor = page.getByLabel("Policy YAML");
await editor.waitFor();
const yaml = await editor.inputValue();
await editor.fill(yaml.replace("version: 1", "version: 2").replace("150000", "100000"));
await page.getByRole("button", { name: "Run tests" }).click();
await page.getByText("3 of 3 passed").waitFor();
await shot("07-policies", true);

await nav("Controls");
await page.getByRole("button", { name: "Pause all runs" }).waitFor();
await shot("08-controls");

await nav("Audit");
await page.getByRole("button", { name: /documents\.extract/ }).first().click();
await page.getByRole("heading", { name: "What left our system" }).waitFor();
await shot("09-audit", true);

await nav("Employees");
await page.getByRole("button", { name: "Show record for Kiran Rao" }).click().catch(() => {});
await shot("10-employees");

await nav("Data requests");
await page.getByRole("rowheader", { name: "Neha Gupta" }).waitFor();
await shot("11-data-requests");

await nav("Ask Relay");
await page.getByRole("button", { name: "How many leaves do I get in probation?" }).click();
await page.getByText(/Source: Leave during probation/).waitFor();
await shot("12-ask-relay");

await browser.close();
