import { apiJson, parseApiError } from "./shared";

// 请求管理 — 内存请求簿记(/api/requests*)。与后端 gateway/api/requests.py 的
// RequestInfoResponse / RequestDetailResponse / 详情 SSE 帧同形(手写镜像)。
// 注意:类型名是 RequestRecord 而非 RequestInfo —— 后者是 DOM 的 fetch 入参类型。
export type RequestStatus =
  | "running"
  | "cancelling"
  | "completed"
  | "cancelled"
  | "client_closed"
  | "failed";

export interface RequestUsage {
  input_tokens: number;
  output_tokens: number;
  cache_tokens: number;
  prompt_tokens: number;
}

export interface RequestRecord {
  id: string;
  model: string;
  alias: string;
  method: string;
  path: string;
  stream: boolean;
  status: RequestStatus;
  started_at: number;
  finished_at: number | null;
  request_preview: string;
  finish_reason: string | null;
  http_status: number | null;
  error: string | null;
  has_reasoning: boolean;
  has_tool_calls: boolean;
  usage: RequestUsage | null;
}

export interface RequestsResponse {
  data: RequestRecord[];
}

export interface RequestDetail extends RequestRecord {
  input: unknown | null;
  input_text: string | null;
  text: string;
  seq: number;
  truncated: boolean;
}

export type RequestStreamEvent =
  | {
      type: "snapshot";
      seq: number;
      text: string;
      truncated: boolean;
      input?: unknown | null;
      input_text?: string | null;
      request: RequestRecord;
    }
  | { type: "delta"; seq: number; text: string }
  | { type: "state"; request: RequestRecord };

export const REQUEST_TAIL_LIMIT = 262144;

export async function fetchRequest(
  id: string,
  tail = REQUEST_TAIL_LIMIT,
): Promise<RequestDetail> {
  return apiJson<RequestDetail>(`/api/requests/${encodeURIComponent(id)}?tail=${tail}`);
}

export async function cancelRequest(id: string): Promise<void> {
  // 后端返回 202 + 空 body(与 /api/models/{alias}/start 同形),不能走 apiJson
  // —— 那会去 res.json() 并抛 "Unexpected end of JSON input"。
  const res = await fetch(`/api/requests/${encodeURIComponent(id)}/cancel`, { method: "POST" });
  if (!res.ok) throw await parseApiError(res);
}

export async function clearCancelledRequests(model?: string): Promise<{ cleared: number }> {
  const qs = model ? `?model=${encodeURIComponent(model)}` : "";
  return apiJson<{ cleared: number }>(`/api/requests/cancelled/clear${qs}`, { method: "POST" });
}
