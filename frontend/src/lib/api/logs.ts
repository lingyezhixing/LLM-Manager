import { apiJson } from "./shared";

// 日志查看页 — persistent session logs (/api/logs/*)。LogLine matches the SSE frame the
// backend emits on /api/logs/sessions/{id}/stream (LogLineResponse in gateway/api/logs_schemas.py;
// captured/leveled in data/logs.py)。
export interface LogLine {
  id: number; ts: number; stream: "out" | "err" | "sys";
  level: "info" | "ok" | "warn" | "error"; text: string;
}

// 日志搜索 / 翻页(本次会话全量在后端,前端按需取一页)。
export interface LogSearch { matches: number[]; total: number; }

export interface LogSession {
  id: number;
  type: "system" | "model";
  model_name: string | null;
  alias: string | null;
  start_time: number;
  end_time: number | null;
  status: "running" | "ended";
  duration_s: number | null;
  line_count: number;
}

export async function fetchSessions(
  opts: { type?: "system" | "model"; model?: string; limit?: number } = {},
): Promise<LogSession[]> {
  const qs = new URLSearchParams();
  if (opts.type) qs.set("type", opts.type);
  if (opts.model) qs.set("model", opts.model);
  if (opts.limit) qs.set("limit", String(opts.limit));
  return apiJson<LogSession[]>(`/api/logs/sessions?${qs}`);
}

export async function fetchSessionLines(
  sessionId: number, before: number, limit = 1500, level?: string,
): Promise<LogLine[]> {
  const qs = new URLSearchParams({ before: String(before), limit: String(limit) });
  if (level) qs.set("level", level);
  return apiJson<LogLine[]>(`/api/logs/sessions/${sessionId}/lines?${qs}`);
}

export async function searchSessionLogs(
  sessionId: number, q: string, level?: string,
): Promise<LogSearch> {
  const qs = new URLSearchParams({ q, session_id: String(sessionId) });
  if (level) qs.set("level", level);
  // /api/logs/search 的 matches 是 {session_id, line} 对象数组(跨会话检索);
  // 映射为行 id 以满足共享 hook 的 LogSearch(matches: number[]) 契约——行 id 全局唯一。
  const d = await apiJson<{ total: number; matches: { session_id: number; line: LogLine }[] }>(`/api/logs/search?${qs}`);
  return { total: d.total, matches: d.matches.map((m) => m.line.id) };
}
