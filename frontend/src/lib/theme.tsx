import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type Theme = "dark" | "light" | "warm";
const STORE_KEY = "lhm-theme";
const DEFAULT: Theme = "dark";
const THEME_VALUES: ReadonlySet<Theme> = new Set(["dark", "light", "warm"]);

type Ctx = { theme: Theme; setTheme: (t: Theme) => void };
const ThemeContext = createContext<Ctx>({ theme: DEFAULT, setTheme: () => {} });

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem(STORE_KEY);
    return saved && THEME_VALUES.has(saved as Theme) ? (saved as Theme) : DEFAULT;
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(STORE_KEY, theme);
  }, [theme]);
  return <ThemeContext.Provider value={{ theme, setTheme }}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  return useContext(ThemeContext);
}
