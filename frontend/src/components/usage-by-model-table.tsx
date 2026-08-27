import { useState } from "react";

import { useQuery } from "@tanstack/react-query";

import { Card, Empty, Skeleton } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { SourcePills } from "@/components/source-pills";
import { fetchUsageByModel, fetchUsageCost, type UsageSource, type UsageSeriesParams } from "@/lib/api";
import { errMsg, formatCost, formatCount, formatLatency, formatPercent, formatTokens } from "@/lib/format";
import { EMPTY_REQUESTS } from "@/lib/usage-range";
import { qk } from "@/lib/api/keys";

type SortKey = "input_tokens" | "output_tokens" | "cache_n" | "request_count" | "hit_rate" | "latency_ms" | "cost";

export function UsageByModelTable({
  params,
  refetch,
}: {
  params: UsageSeriesParams;
  refetch: number | false;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("input_tokens");
  const [desc, setDesc] = useState(true);
  const [source, setSource] = useState<UsageSource>("all");
  // 归属过滤在服务端完成(by-model 行 / cost by_model 均带 source)
  const filteredParams: UsageSeriesParams = { ...params, source };
  const { data, isLoading, isError, error, refetch: refetchByModel } = useQuery({
    queryKey: qk.usageByModel(filteredParams),
    queryFn: () => fetchUsageByModel(filteredParams),
    refetchInterval: refetch,
  });
  const costQ = useQuery({
    queryKey: qk.usageCost(filteredParams),
    queryFn: () => fetchUsageCost(filteredParams),
    refetchInterval: refetch,
  });
  const costOf = new Map((costQ.data?.by_model ?? []).map((r) => [r.model, r.cost]));

  // 排序仅对已加载数据生效;空/加载/错误态不参与
  const rows = data ? [...data].sort((a, b) => {
    if (sortKey === "cost") {
      const d = (costOf.get(a.model) ?? 0) - (costOf.get(b.model) ?? 0);
      return desc ? -d : d;
    }
    const d = a[sortKey] - b[sortKey];
    return desc ? -d : d;
  }) : [];
  const onSort = (k: SortKey) => {
    if (k === sortKey) setDesc(!desc);
    else {
      setSortKey(k);
      setDesc(true);
    }
  };

  return (
    <Card>
      {costQ.isError && (
        <ErrorState className="mb-3" prefix="成本加载失败" message={errMsg(costQ.error)} onRetry={() => costQ.refetch()} />
      )}
      <div className="mb-3 flex justify-end">
        <SourcePills value={source} onChange={setSource} />
      </div>
      {isError ? (
        <ErrorState message={errMsg(error)} onRetry={() => refetchByModel()} />
      ) : isLoading ? (
        <Skeleton rows={6} />
      ) : !data || data.length === 0 ? (
        <Empty label={EMPTY_REQUESTS} />
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr>
              <Th label="模型 / 归属" />
              <ThNum label="输入" k="input_tokens" sortKey={sortKey} desc={desc} onSort={onSort} />
              <ThNum label="输出" k="output_tokens" sortKey={sortKey} desc={desc} onSort={onSort} />
              <ThNum label="缓存命中" k="cache_n" sortKey={sortKey} desc={desc} onSort={onSort} />
              <ThNum label="请求数" k="request_count" sortKey={sortKey} desc={desc} onSort={onSort} />
              <th className="p-2 text-left text-xs font-medium text-muted-foreground">占比</th>
              <ThNum label="命中率" k="hit_rate" sortKey={sortKey} desc={desc} onSort={onSort} />
              <ThNum label="平均延迟" k="latency_ms" sortKey={sortKey} desc={desc} onSort={onSort} />
              <ThNum label="成本" k="cost" sortKey={sortKey} desc={desc} onSort={onSort} />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.model} className="border-t border-border">
                <td className="p-2">
                  <div className="flex items-center gap-2">
                    {r.model}
                    <SourceBadge source={r.source} />
                  </div>
                </td>
                <td className="p-2 text-right font-mono tabular-nums">{formatTokens(r.input_tokens)}</td>
                <td className="p-2 text-right font-mono tabular-nums">{formatTokens(r.output_tokens)}</td>
                <td className="p-2 text-right font-mono tabular-nums text-success">{formatTokens(r.cache_n)}</td>
                <td className="p-2 text-right font-mono tabular-nums">{formatCount(r.request_count)}</td>
                <td className="p-2">
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                      <div className="h-full rounded-full bg-primary" style={{ width: `${(r.share * 100).toFixed(1)}%` }} />
                    </div>
                    <span className="w-9 text-right font-mono text-xs text-muted-foreground">{formatPercent(r.share)}</span>
                  </div>
                </td>
                <td className="p-2 text-right font-mono tabular-nums">{formatPercent(r.hit_rate, 1)}</td>
                <td className="p-2 text-right font-mono tabular-nums">{formatLatency(r.latency_ms)}</td>
                <td className="p-2 text-right font-mono tabular-nums">{costQ.data ? formatCost(costOf.get(r.model) ?? 0) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function Th({ label }: { label: string }) {
  return <th className="p-2 text-left text-xs font-medium text-muted-foreground">{label}</th>;
}

/** 归属徽标:模型名按命名空间归属,徽标即行 source——本地→灰、云端→primary。 */
function SourceBadge({ source }: { source: string }) {
  const cloud = source === "cloud";
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-xs ${
        cloud ? "bg-primary-accent/12 text-primary-accent" : "bg-muted text-muted-foreground"
      }`}
    >
      {cloud ? "云端" : "本地"}
    </span>
  );
}

function ThNum({
  label,
  k,
  sortKey,
  desc,
  onSort,
}: {
  label: string;
  k: SortKey;
  sortKey: SortKey;
  desc: boolean;
  onSort: (k: SortKey) => void;
}) {
  const active = k === sortKey;
  return (
    <th className="p-2 text-right text-xs font-medium">
      <button
        type="button"
        onClick={() => onSort(k)}
        className={active ? "text-foreground" : "text-muted-foreground hover:text-foreground"}
      >
        {label}{active ? (desc ? " ↓" : " ↑") : ""}
      </button>
    </th>
  );
}
