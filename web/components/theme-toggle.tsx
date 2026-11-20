"use client";

import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

// Light is the default. The choice is per browser; `THEME_SCRIPT` applies it before first paint.
const KEY = "relay-theme";

export const THEME_SCRIPT = `try{if(localStorage.getItem("${KEY}")==="dark"){document.documentElement.classList.add("dark");document.documentElement.style.colorScheme="dark"}}catch(e){}`;

function apply(dark: boolean) {
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
}

/** Switches the console between the light and dark themes and remembers the choice. */
export function ThemeToggle({ className }: { className?: string }) {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);

  const toggle = () => {
    const next = !dark;
    setDark(next);
    apply(next);
    try {
      localStorage.setItem(KEY, next ? "dark" : "light");
    } catch {
      // Storage can be blocked; the theme still changes for this page view.
    }
  };

  const label = dark ? "Switch to light theme" : "Switch to dark theme";
  return (
    <button type="button" onClick={toggle} aria-label={label} title={label} className={className}>
      {dark ? <Sun className="size-4" aria-hidden /> : <Moon className="size-4" aria-hidden />}
    </button>
  );
}
