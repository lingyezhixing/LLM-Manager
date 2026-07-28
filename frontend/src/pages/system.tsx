import { useState } from "react";
import { GeneralPanel } from "@/components/system/general-panel";
import { ModelDefPanel } from "@/components/system/model-def-panel";
import { RestartBanner } from "@/components/system/restart-banner";
import { SystemInfoPanel } from "@/components/system/system-info-panel";
import { SystemNav, type SystemZone } from "@/components/system/system-nav";
import { ZonePlaceholder } from "@/components/system/zone-placeholder";
import { useRestartApp, useRestartStatus } from "@/lib/use-config";

const PLACEHOLDER_LABEL: Record<Exclude<SystemZone, "general" | "models" | "info">, string> = {
  network: "网络",
  claude: "Claude",
  logs: "日志",
};

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
        <div className="mb-4 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          {restartError}
        </div>
      )}
      {restarting && (
        <div className="mb-4 rounded-md border border-border bg-muted p-3 text-sm text-muted-foreground">
          正在重启,页面将在恢复后自动刷新…
        </div>
      )}
      <div className="mt-4 grid gap-6 lg:grid-cols-[minmax(0,180px)_minmax(0,1fr)]">
        <SystemNav active={zone} onSelect={setZone} />
        <div>
          {zone === "general" && <GeneralPanel />}
          {zone === "models" && <ModelDefPanel />}
          {zone === "info" && <SystemInfoPanel />}
          {zone !== "general" && zone !== "models" && zone !== "info" && (
            <ZonePlaceholder label={PLACEHOLDER_LABEL[zone]} />
          )}
        </div>
      </div>
    </>
  );
}
