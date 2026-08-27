import { useState } from "react";
import { Button } from "@/components/ui/button";
import { DatabasePanel } from "@/components/system/database-panel";
import { GeneralPanel } from "@/components/system/general-panel";
import { ModelDefPanel } from "@/components/system/model-def-panel";
import { RestartBanner } from "@/components/system/restart-banner";
import { ZoneNav } from "@/components/ui/nav-tabs";
import { useConfig, useRestartApp, useRestartStatus, useUpdateProgram } from "@/lib/hooks/use-config";

type SystemZone = "general" | "models" | "database";
const ZONES: readonly { key: SystemZone; label: string }[] = [
  { key: "general", label: "系统配置" },
  { key: "models", label: "本地模型配置" },
  { key: "database", label: "数据库管理" },
];

export default function SystemPage() {
  const [zone, setZone] = useState<SystemZone>("general");
  const { data: rs } = useRestartStatus();
  const { data: cfg } = useConfig();
  const { triggerRestart, restarting, pending, error: restartError } = useRestartApp();
  const restore = useUpdateProgram();

  const onRestore = () => {
    const rp = cfg?.running_program;
    if (!rp) return;
    restore.mutate({
      host: rp.host,
      port: rp.port,
      log_level: rp.log_level,
      claude_settings_path: rp.claude_settings_path,
    });
  };

  return (
    <>
      {rs?.needs_restart && <RestartBanner
        restartFields={rs.restart_fields}
        serving={rs.serving}
        onRestart={triggerRestart}
        onRestore={onRestore}
        restarting={restarting || pending}
        restoring={restore.isPending}
      />}
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
      <div>
        <ZoneNav zones={ZONES} active={zone} onSelect={setZone} />
        <div className="mt-6">
          {zone === "general" && <GeneralPanel />}
          {zone === "models" && <ModelDefPanel />}
          {zone === "database" && <DatabasePanel />}
        </div>
      </div>
    </>
  );
}
