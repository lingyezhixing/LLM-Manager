// Theme context、hook 与常量。与 ThemeProvider 分离,使 provider
// 文件只导出组件(保持 React Fast Refresh 正常 — 见 oxlint
// react/only-export-components)。只留暗/亮两主题。
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
