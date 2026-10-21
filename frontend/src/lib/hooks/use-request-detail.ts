import { useEffect, useRef, useState } from "react";
import { fetchRequest, type RequestRecord, type RequestStreamEvent } from "@/lib/api";

// 详情文本只保留最近 20 万字符:渲染成本与内存都有界。服务端保留全量,
// 但传给浏览器的快照只取尾部(REQUEST_TAIL_LIMIT)。
export const MAX_DISPLAY_CHARS = 200_000;

// 单块目标长度:小 delta 合进当前块,避免"每 token 一个 DOM 节点"。
const BLOCK_TARGET = 4096;

// delta 合批间隔:退化循环恰好是"又长又快",逐条 setState 会把这个面板卡死。
const FLUSH_MS = 100;

const TERMINAL: ReadonlySet<RequestRecord["status"]> = new Set([
  "completed",
  "cancelled",
  "client_closed",
  "failed",
]);

export interface DetailState {
  seq: number;
  blocks: string[];
  chars: number;
  truncated: boolean;
  record: RequestRecord | null;
  input: unknown | null;
  inputText: string | null;
  needsResync: boolean;
}

export const initialDetailState: DetailState = {
  seq: 0,
  blocks: [],
  chars: 0,
  truncated: false,
  record: null,
  input: null,
  inputText: null,
  needsResync: false,
};

function isTerminal(status: RequestRecord["status"]): boolean {
  return TERMINAL.has(status);
}

/** 追加一段文本:小段并入当前块,超窗口从头部丢块。 */
export function foldDelta(state: DetailState, text: string): DetailState {
  if (!text) return state;
  const blocks = state.blocks.slice();
  const last = blocks.length - 1;
  if (last >= 0 && blocks[last].length < BLOCK_TARGET) blocks[last] += text;
  else blocks.push(text);
  let chars = state.chars + text.length;
  let truncated = state.truncated;
  while (chars > MAX_DISPLAY_CHARS && blocks.length > 1) {
    chars -= blocks[0].length;
    blocks.shift();
    truncated = true;
  }
  return { ...state, blocks, chars, truncated };
}

/** seq 守卫:重复/乱序丢弃,跳号标记需要重取快照。 */
export function acceptDelta(state: DetailState, seq: number, text: string): DetailState {
  if (seq <= state.seq) return state;
  const next = foldDelta({ ...state, seq }, text);
  return seq > state.seq + 1 ? { ...next, needsResync: true } : next;
}

export function applySnapshot(
  state: DetailState,
  snap: {
    seq: number;
    text: string;
    truncated: boolean;
    record: RequestRecord;
    input?: unknown | null;
    inputText?: string | null;
  },
): DetailState {
  return {
    ...state,
    seq: snap.seq,
    blocks: snap.text ? [snap.text] : [],
    chars: snap.text.length,
    truncated: snap.truncated,
    record: snap.record,
    input: snap.input ?? null,
    inputText: snap.inputText ?? null,
    needsResync: false,
  };
}

export function applyState(state: DetailState, record: RequestRecord): DetailState {
  return { ...state, record };
}

/**
 * 订阅单条请求的详情 SSE。``id`` 变化即重订阅;终态自动关流。
 * 文本更新走 100ms 合批,避免高频 delta 触发整面板重渲染。
 */
export function useRequestDetail(id: string | null): DetailState {
  const [state, setState] = useState<DetailState>(initialDetailState);
  const pending = useRef<{ seq: number; text: string }[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    pending.current = [];
    setState(initialDetailState);
    if (!id) return;

    const flush = () => {
      timer.current = null;
      const batch = pending.current;
      pending.current = [];
      if (batch.length === 0) return;
      setState((s) => batch.reduce((acc, d) => acceptDelta(acc, d.seq, d.text), s));
    };

    const schedule = () => {
      if (timer.current !== null) return;
      timer.current = setTimeout(flush, FLUSH_MS);
    };

    const es = new EventSource(`/api/requests/${encodeURIComponent(id)}/stream`);
    es.onmessage = (ev) => {
      let msg: RequestStreamEvent;
      try {
        msg = JSON.parse(ev.data) as RequestStreamEvent;
      } catch {
        return;
      }
      if (msg.type === "snapshot") {
        pending.current = [];
        setState((s) =>
          applySnapshot(s, {
            seq: msg.seq,
            text: msg.text,
            truncated: msg.truncated,
            record: msg.request,
            input: msg.input,
            inputText: msg.input_text,
          }),
        );
        if (isTerminal(msg.request.status)) es.close();
        return;
      }
      if (msg.type === "delta") {
        pending.current.push({ seq: msg.seq, text: msg.text });
        schedule();
        return;
      }
      setState((s) => applyState(s, msg.request));
      if (isTerminal(msg.request.status)) es.close();
    };

    return () => {
      es.close();
      if (timer.current !== null) {
        clearTimeout(timer.current);
        timer.current = null;
      }
      pending.current = [];
    };
  }, [id]);

  // 跳号兜底:重取一次详情快照(不期望发生;事件队列满时才可能)。
  useEffect(() => {
    if (!state.needsResync || !id) return;
    let cancelled = false;
    fetchRequest(id)
      .then((d) => {
        if (cancelled) return;
        setState((s) =>
          applySnapshot(s, {
            seq: d.seq,
            text: d.text,
            truncated: d.truncated,
            record: d,
            input: d.input,
            inputText: d.input_text,
          }),
        );
      })
      .catch(() => {
        /* 尽力而为:下一条 delta 会再次触发 */
      });
    return () => {
      cancelled = true;
    };
  }, [state.needsResync, id]);

  return state;
}
