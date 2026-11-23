// Render an SVG to a 2x PNG with the installed Chrome. Usage: node scripts/render-svg.mjs in.svg out.png
import { readFileSync } from "node:fs";
import { chromium } from "@playwright/test";

const [input, output] = process.argv.slice(2);
const svg = readFileSync(input, "utf8");
const width = Number(/width="(\d+)"/.exec(svg)?.[1] ?? 1600);
const height = Number(/height="(\d+)"/.exec(svg)?.[1] ?? 1000);
const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 2 });
await page.setContent(`<html><body style="margin:0">${svg}</body></html>`);
await page.screenshot({ path: output, clip: { x: 0, y: 0, width, height } });
await browser.close();
console.log("saved", output);
