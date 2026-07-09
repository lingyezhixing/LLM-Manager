import { useTheme, type Theme } from "@/lib/theme-context";
import { Button } from "@/components/ui/button";

const THEMES: ReadonlyArray<[Theme, string]> = [
  ["dark", "深色"],
  ["light", "浅色"],
  ["warm", "暖灰"],
];

export function ThemeSwitcher() {
  const { theme, setTheme } = useTheme();
  return (
    <div className="flex gap-1">
      {THEMES.map(([key, label]) => (
        <Button
          key={key}
          size="sm"
          variant={theme === key ? "default" : "ghost"}
          onClick={() => setTheme(key)}
        >
          {label}
        </Button>
      ))}
    </div>
  );
}
