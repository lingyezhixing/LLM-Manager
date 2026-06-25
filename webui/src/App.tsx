import { ModelsDashboard } from "@/components/models-dashboard";
import { ThemeSwitcher } from "@/components/theme-switcher";

export default function App() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b border-border">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-3">
          <span className="font-semibold">LLM-Manager</span>
          <ThemeSwitcher />
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-4 py-6">
        <h1 className="mb-4 text-lg font-semibold">模型</h1>
        <ModelsDashboard />
      </main>
    </div>
  );
}
