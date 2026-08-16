import { useState } from "react";

import { useQuery } from "@tanstack/react-query";

import { Card, Empty, Loading } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { fetchUsageByModel, fetchUsageCost, type UsageSeriesParams } from "@/lib/api";
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
  const { data, isLoading, isError, error, refetch: refetchByModel } = useQuery({
    queryKey: qk.usageByModel(params),
    queryFn: () => fetchUsageByModel(params),
    refetchInterval: refetch,
  });
  const costQ = useQuery({
    queryKey: qk.usageCost(params),
    queryFn: () => fetchUsageCost(params),
    refetchInterval: refetch,
  });
  const costOf = new Map((costQ.data?.by_model ?? []).map((r) => [r.model, r.cost]));
  const [sortKey, setSortKey] = useState<SortKey>("input_tokens");
  const [desc, setDesc] = useState(true);

  if (isError) return <Card><ErrorState message={errMsg(error)} onRetry={() => refetchByModel()} /></Card>;
  if (isLoading) return <Card><Loading /></Card>;
  if (!data || data.length === 0) return <Card><Empty label={EMPTY_REQUESTS} /></Card>;

  const rows = [...data].sort((a, b) => {
    if (sortKey === "cost") {
      const d = (costOf.get(a.model) ?? 0) - (costOf.get(b.model) ?? 0);
      return desc ? -d : d;
    }
    const d = a[sortKey] - b[sortKey];
    return desc ? -d : d;
  });
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
      <table className="w-full text-sm">
        <thead>
          <tr>
            <Th label="模型" />
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
              <td className="p-2">{r.model}</td>
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
    </Card>
  );
}

function Th({ label }: { label: string }) {
  return <th className="p-2 text-left text-xs font-medium text-muted-foreground">{label}</th>;
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
