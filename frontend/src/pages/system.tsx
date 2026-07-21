import { useState } from "react";
import { PageHeader } from "@/components/page-header";
import { GeneralPanel } from "@/components/system/general-panel";
import { RestartBanner } from "@/components/system/restart-banner";
import { SystemInfoPanel } from "@/components/system/system-info-panel";
import { SystemNav, type SystemZone } from "@/components/system/system-nav";
import { ZonePlaceholder } from "@/components/system/zone-placeholder";
import { useRestartStatus } from "@/lib/use-config";

const PLACEHOLDER_LABEL: Record<Exclude<SystemZone, "general" | "info">, string> = {
  models: "模型",
  network: "网络",
  claude: "Claude",
  logs: "日志",
};

export default function SystemPage() {
  const [zone, setZone] = useState<SystemZone>("general");
  const { data: rs } = useRestartStatus();
  const fieldsKey = rs?.restart_fields.join(",") ?? "";
  const [dismissedKey, setDismissedKey] = useState<string | null>(null);
  const showBanner = !!rs?.needs_restart && dismissedKey !== fieldsKey;

  return (
    <>
      <PageHeader title="系统配置" subtitle="程序 · 模型 · 日志 · 网络 · 系统" />
      {showBanner && rs && (
        <RestartBanner
          restartFields={rs.restart_fields}
          serving={rs.serving}
          onDismiss={() => setDismissedKey(fieldsKey)}
        />
      )}
      <div className="mt-4 grid gap-6 lg:grid-cols-[minmax(0,180px)_minmax(0,1fr)]">
        <SystemNav active={zone} onSelect={setZone} />
        <div>
          {zone === "general" && <GeneralPanel />}
          {zone === "info" && <SystemInfoPanel />}
          {zone !== "general" && zone !== "info" && (
            <ZonePlaceholder label={PLACEHOLDER_LABEL[zone]} />
          )}
        </div>
      </div>
    </>
  );
}
