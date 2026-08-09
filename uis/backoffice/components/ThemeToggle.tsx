"use client";

import { useEffect, useState } from "react";

const STORAGE_KEY = "healthcore.theme";

type Theme = "dark" | "light";

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("dark");

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") {
      setTheme(stored);
    }
  }, []);

  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    window.localStorage.setItem(STORAGE_KEY, next);
    document.documentElement.setAttribute("data-theme", next);
  };

  return (
    <button type="button" onClick={toggle} className="theme-toggle">
      {theme === "dark" ? "Modo claro" : "Modo oscuro"}
    </button>
  );
}
