import { describe, expect, it } from "vitest";
import {
  MAX_DISPLAY_CHARS,
  acceptDelta,
  applySnapshot,
  applyState,
  foldDelta,
  initialDetailState,
  type DetailState,
} from "@/lib/hooks/use-request-detail";
import type { RequestRecord } from "@/lib/api";

const record = (status: RequestRecord["status"]): RequestRecord => ({
  id: "r-000001",
  model: "m1",
  alias: "m1",
  method: "POST",
  path: "/v1/chat/completions",
  stream: true,
  status,
  started_at: 1700000000,
  finished_at: status === "running" ? null : 1700000010,
  request_preview: "hi",
  finish_reason: null,
  http_status: 200,
  error: null,
  has_reasoning: false,
  has_tool_calls: false,
  usage: null,
});

describe("foldDelta", () => {
  it("追加文本,空串不产生新块", () => {
    const s = foldDelta(initialDetailState, "abc");
    expect(s.blocks.join("")).toBe("abc");
    expect(foldDelta(s, "")).toBe(s);
  });

  it("超过 20 万字符时从头丢块并置 truncated", () => {
    let s: DetailState = initialDetailState;
    for (let i = 0; i < 3; i++) s = foldDelta(s, "x".repeat(80_000));
    expect(s.truncated).toBe(true);
    expect(s.chars).toBeLessThanOrEqual(MAX_DISPLAY_CHARS);
    expect(s.blocks.join("").endsWith("x")).toBe(true);
  });

  it("小 delta 合进同一块,不产生逐条块", () => {
    let s = foldDelta(initialDetailState, "a");
    s = foldDelta(s, "b");
    s = foldDelta(s, "c");
    expect(s.blocks).toEqual(["abc"]);
  });
});

describe("acceptDelta", () => {
  it("重复或乱序 seq 被忽略", () => {
    const s: DetailState = { ...initialDetailState, seq: 3 };
    expect(acceptDelta(s, 3, "x")).toBe(s);
    expect(acceptDelta(s, 2, "x")).toBe(s);
  });

  it("跳号置 needsResync", () => {
    const s: DetailState = { ...initialDetailState, seq: 1 };
    const next = acceptDelta(s, 3, "x");
    expect(next.needsResync).toBe(true);
    expect(next.seq).toBe(3);
  });

  it("连续 seq 正常追加且不置 needsResync", () => {
    const s: DetailState = { ...initialDetailState, seq: 1 };
    const next = acceptDelta(s, 2, "x");
    expect(next.needsResync).toBe(false);
    expect(next.blocks.join("")).toBe("x");
  });
});

describe("applySnapshot / applyState", () => {
  it("snapshot 重置文本与序号,并记录服务端截断标记", () => {
    const dirty: DetailState = { ...foldDelta(initialDetailState, "old"), seq: 9 };
    const next = applySnapshot(dirty, {
      seq: 4,
      text: "tail",
      truncated: true,
      record: record("running"),
    });
    expect(next.blocks).toEqual(["tail"]);
    expect(next.seq).toBe(4);
    expect(next.truncated).toBe(true);
    expect(next.record?.status).toBe("running");
  });

  it("state 事件用新元数据替换记录", () => {
    const running = applyState(initialDetailState, record("running"));
    expect(running.record?.status).toBe("running");
    const done = applyState(running, record("cancelled"));
    expect(done.record?.status).toBe("cancelled");
  });
});
