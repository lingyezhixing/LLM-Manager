// Theme context, hook, and constants. Separated from ThemeProvider so the provider's
// file only exports a component (keeps React Fast Refresh happy — see oxlint
// react/only-export-components). warm 主题 2026-08-04 退役(设计重做,只留暗/亮)。
import { createContext, useContext } from "react";

export type Theme = "dark" | "light";

export const THEME_STORE_KEY = "lhm-theme";
export const THEME_DEFAULT: Theme = "dark";
export const THEME_VALUES: ReadonlySet<Theme> = new Set(["dark", "light"]);

type Ctx = { theme: Theme; setTheme: (t: Theme) => void };
export const ThemeContext = createContext<Ctx>({ theme: THEME_DEFAULT, setTheme: () => {} });

export function useTheme() {
  return useContext(ThemeContext);
}
