import { useEffect, useState } from "react";

import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";

import { fetchUsageByModel, fetchUsageRequests, type RequestsParams, type UsageSeriesParams } from "@/lib/api";
import { formatCount, formatLatency, formatTokens } from "@/lib/format";

const LIMIT = 50;

function buildParams(params: UsageSeriesParams, model: string, before: number | undefined): RequestsParams {
  const p: RequestsParams = { limit: LIMIT };
  if ("range" in params) p.range = params.range;
  else {
    p.start = params.start;
    p.end = params.end;
  }
  if (model) p.model = model;
  if (before !== undefined) p.before = before;
  return p;
}

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function UsageRequestTable({ params }: { params: UsageSeriesParams }) {
  const [model, setModel] = useState("");
  const [before, setBefore] = useState<number | undefined>(undefined);
  const [history, setHistory] = useState<(number | undefined)[]>([]);

  const paramsKey = JSON.stringify(params);
  useEffect(() => {
    setBefore(undefined);
    setHistory([]);
  }, [paramsKey]);

  const { data: models } = useQuery({
    queryKey: ["usage", "by-model", params],
    queryFn: () => fetchUsageByModel(params),
  });
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["usage", "requests", params, model, before],
    queryFn: () => fetchUsageRequests(buildParams(params, model, before)),
  });

  const page = history.length + 1;
  const totalPages = data ? Math.max(1, Math.ceil(data.total / LIMIT)) : 1;

  const next = () => {
    if (!data || !data.has_more || data.rows.length === 0) return;
    setHistory((h) => [...h, before]);
    setBefore(data.rows[data.rows.length - 1].id);
  };
  const prev = () => {
    if (history.length === 0) return;
    const h = [...history];
    const prevCursor = h.pop();
    setHistory(h);
    setBefore(prevCursor);
  };

  return (
    <div className="rounded-lg border border-border p-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">单请求明细</span>
        <div className="flex items-center gap-2">
          <select
            value={model}
            onChange={(e) => {
              setModel(e.target.value);
              setBefore(undefined);
              setHistory([]);
            }}
            className="rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground"
          >
            <option value="">全部模型</option>
            {models?.map((m) => (
              <option key={m.model} value={m.model}>{m.model}</option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => refetch()}
            className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <RefreshCw className="h-3 w-3" /> 刷新
          </button>
        </div>
      </div>
      {isLoading || !data ? (
        <div className="flex h-24 items-center justify-center text-sm text-muted-foreground">加载中…</div>
      ) : data.rows.length === 0 ? (
        <div className="flex h-24 items-center justify-center text-sm text-muted-foreground">该时间范围内暂无请求</div>
      ) : (
        <>
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="p-2 text-left text-xs font-medium text-muted-foreground">时间</th>
                <th className="p-2 text-left text-xs font-medium text-muted-foreground">模型</th>
                <th className="p-2 text-right text-xs font-medium text-muted-foreground">输入</th>
                <th className="p-2 text-right text-xs font-medium text-muted-foreground">输出</th>
                <th className="p-2 text-right text-xs font-medium text-muted-foreground">缓存</th>
                <th className="p-2 text-right text-xs font-medium text-muted-foreground">延迟</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.id} className="border-t border-border">
                  <td className="p-2 tabular-nums text-muted-foreground">{fmtTime(r.end_time)}</td>
                  <td className="p-2">{r.model}</td>
                  <td className="p-2 text-right tabular-nums">{formatTokens(r.input_tokens)}</td>
                  <td className="p-2 text-right tabular-nums">{formatTokens(r.output_tokens)}</td>
                  <td className="p-2 text-right tabular-nums text-success">{r.cache_n > 0 ? formatTokens(r.cache_n) : "0"}</td>
                  <td className="p-2 text-right tabular-nums">{formatLatency(r.latency_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
            <span>共 {formatCount(data.total)} 条 · 第 {page} / {totalPages} 页</span>
            <div className="flex gap-1">
              <button
                type="button"
                onClick={prev}
                disabled={history.length === 0}
                className="rounded border border-border px-2 py-0.5 disabled:opacity-40"
              >
                ‹ 上一页
              </button>
              <button
                type="button"
                onClick={next}
                disabled={!data.has_more}
                className="rounded border border-border px-2 py-0.5 disabled:opacity-40"
              >
                下一页 ›
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
