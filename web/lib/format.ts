import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function percent(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

export function inr(value: string | null, digits = 2): string {
  if (value === null) return "—";
  return `₹${Number(value).toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

export function duration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  const totalMinutes = Math.round(seconds / 60);
  if (totalMinutes < 60) return `${totalMinutes}m`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
}

export function dateTime(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export function humanize(id: string): string {
  const text = id.replaceAll("_", " ").replace(/\bhr\b/g, "HR").replace(/\bit spend\b/g, "IT spend");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** "Due in 3h", "Overdue by 12m". Pass `now` from a ticking state so it stays current. */
export function slaLabel(due: string | null, now: number): { text: string; overdue: boolean } {
  if (!due) return { text: "No SLA", overdue: false };
  const seconds = (new Date(due).getTime() - now) / 1000;
  return seconds >= 0
    ? { text: `Due in ${duration(seconds)}`, overdue: false }
    : { text: `Overdue by ${duration(-seconds)}`, overdue: true };
}

/** A small line diff for the policy editor. Common prefix and suffix, then what changed between. */
export function lineDiff(before: string, after: string): { kind: "same" | "add" | "del"; text: string }[] {
  const a = before.split("\n");
  const b = after.split("\n");
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start += 1;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA -= 1;
    endB -= 1;
  }
  return [
    ...a.slice(0, start).map((text) => ({ kind: "same" as const, text })),
    ...a.slice(start, endA).map((text) => ({ kind: "del" as const, text })),
    ...b.slice(start, endB).map((text) => ({ kind: "add" as const, text })),
    ...a.slice(endA).map((text) => ({ kind: "same" as const, text })),
  ];
}
