"use client";

/**
 * theme.tsx
 * Centralised light/dark theme controller for the web app. The desktop app
 * (src/ui/theme.py) is dark-only; the web build adds an opt-in light theme.
 *
 * The active theme is a single class on <html> ("dark" | "light"). Every color
 * in globals.css is a CSS variable keyed off that class, so toggling the class
 * re-themes the whole app with no re-render of component styles. The chosen
 * theme is persisted in the existing UI-prefs localStorage store and mirrored
 * across tabs via the storage event (usePreferences already subscribes).
 *
 * An inline pre-hydration script (rendered in layout.tsx) sets the class before
 * first paint so there is no dark->light flash on load.
 */

import { createContext, useCallback, useContext, useEffect } from "react";
import {
  getPreferences,
  setThemePref,
  usePreferences,
  type ThemeMode,
} from "@/lib/preferences";

interface ThemeContextValue {
  theme: ThemeMode;
  setTheme: (t: ThemeMode) => void;
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

/** Apply the theme class to <html>, removing the other. */
function applyThemeClass(theme: ThemeMode) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.classList.remove("dark", "light");
  root.classList.add(theme);
  root.style.colorScheme = theme;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const { prefs } = usePreferences();
  const theme = prefs.theme;

  // Keep the <html> class in sync whenever the persisted theme changes
  // (including cross-tab updates surfaced through usePreferences).
  useEffect(() => {
    applyThemeClass(theme);
  }, [theme]);

  const setTheme = useCallback((t: ThemeMode) => {
    setThemePref(t);
    applyThemeClass(t);
  }, []);

  const toggleTheme = useCallback(() => {
    const next: ThemeMode =
      getPreferences().theme === "dark" ? "light" : "dark";
    setThemePref(next);
    applyThemeClass(next);
  }, []);

  return (
    <ThemeContext.Provider value={{ theme, setTheme, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme must be used within a ThemeProvider");
  }
  return ctx;
}

/**
 * Inline script string executed before hydration to set the initial theme
 * class from localStorage, avoiding a flash of the wrong theme. Kept in sync
 * with the KEY / shape used by preferences.ts (tt.ui.prefs.v3).
 */
export const THEME_INIT_SCRIPT = `
(function(){
  try {
    var raw = localStorage.getItem("tt.ui.prefs.v3");
    var theme = "dark";
    if (raw) {
      var p = JSON.parse(raw);
      if (p && p.theme === "light") theme = "light";
    }
    var r = document.documentElement;
    r.classList.remove("dark","light");
    r.classList.add(theme);
    r.style.colorScheme = theme;
  } catch (e) {
    document.documentElement.classList.add("dark");
  }
})();
`;
