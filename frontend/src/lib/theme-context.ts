// Theme context, hook, and constants. Separated from ThemeProvider so the provider's
// file only exports a component (keeps React Fast Refresh happy — see oxlint
// react/only-export-components).
import { createContext, useContext } from "react";

export type Theme = "dark" | "light" | "warm";

export const THEME_STORE_KEY = "lhm-theme";
export const THEME_DEFAULT: Theme = "dark";
export const THEME_VALUES: ReadonlySet<Theme> = new Set(["dark", "light", "warm"]);

type Ctx = { theme: Theme; setTheme: (t: Theme) => void };
export const ThemeContext = createContext<Ctx>({ theme: THEME_DEFAULT, setTheme: () => {} });

export function useTheme() {
  return useContext(ThemeContext);
}
