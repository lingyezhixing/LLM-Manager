import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ClaudePanel } from "@/components/system/claude-panel";
import { DatabasePanel } from "@/components/system/database-panel";
import { GeneralPanel } from "@/components/system/general-panel";
import { ModelDefPanel } from "@/components/system/model-def-panel";
import { RestartBanner } from "@/components/system/restart-banner";
import { SystemNav, type SystemZone } from "@/components/system/system-nav";
import { WolPanel } from "@/components/system/wol-panel";
import { useRestartApp, useRestartStatus } from "@/lib/use-config";

export default function SystemPage() {
  const [zone, setZone] = useState<SystemZone>("general");
  const { data: rs } = useRestartStatus();
  const { triggerRestart, restarting, error: restartError } = useRestartApp();
  const fieldsKey = rs?.restart_fields.join(",") ?? "";
  const [dismissedKey, setDismissedKey] = useState<string | null>(null);
  const showBanner = !!rs?.needs_restart && dismissedKey !== fieldsKey;

  return (
    <>
      {showBanner && rs && (
        <RestartBanner
          restartFields={rs.restart_fields}
          serving={rs.serving}
          onDismiss={() => setDismissedKey(fieldsKey)}
          onRestart={triggerRestart}
          restarting={restarting}
        />
      )}
      {restartError && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          <span>{restartError}</span>
          <Button size="sm" variant="ghost" onClick={() => window.location.reload()}>刷新页面</Button>
        </div>
      )}
      {restarting && (
        <div className="mb-4 rounded-md border border-border bg-muted p-3 text-sm text-muted-foreground">
          正在重启,页面将在恢复后自动刷新…
        </div>
      )}
      <div className="mt-4">
        <SystemNav active={zone} onSelect={setZone} />
        <div className="mt-6">
          {zone === "general" && <GeneralPanel />}
          {zone === "models" && <ModelDefPanel />}
          {zone === "network" && <WolPanel />}
          {zone === "claude" && <ClaudePanel />}
          {zone === "database" && <DatabasePanel />}
        </div>
      </div>
    </>
  );
}
