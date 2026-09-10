import { useEffect, useState, type ReactNode } from "react";

import { THEME_DEFAULT, THEME_STORE_KEY, THEME_VALUES, ThemeContext, type Theme } from "@/lib/theme-context";

/** 将当前主题应用到 <html data-theme> 并持久化到 localStorage。 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem(THEME_STORE_KEY);
    return saved && THEME_VALUES.has(saved as Theme) ? (saved as Theme) : THEME_DEFAULT;
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_STORE_KEY, theme);
  }, [theme]);
  return <ThemeContext.Provider value={{ theme, setTheme }}>{children}</ThemeContext.Provider>;
}
