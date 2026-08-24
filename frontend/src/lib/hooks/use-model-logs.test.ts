import { describe, expect, it } from "vitest";
import type { LogLine } from "@/lib/api";
import { LOG_PAGE_LIMIT } from "@/lib/api";
import { viewerReducer, type ViewerState } from "@/lib/hooks/use-model-logs";

/** 起始状态构造器:与 initialState 同形(initialState 未导出,用 reset 取)。 */
const base = (): ViewerState => viewerReducer(undefined as unknown as ViewerState, { t: "reset" });

const line = (id: number): LogLine =>
  ({ id, ts: 1700000000 + id, text: `L${id}`, level: "info" as LogLine["level"], stream: "out" as LogLine["stream"] });

const WINDOW = LOG_PAGE_LIMIT;

describe("viewerReducer", () => {
  it("reset 复位全部字段", () => {
    const s = base();
    expect(s).toEqual({
      liveLines: [], historyPrefix: [], historyPage: null, following: true,
      newCount: 0, matches: [], matchTotal: 0, matchIdx: -1,
      hasSearched: false, searching: false, scrollTargetId: null, atOldest: false,
    });
  });

  it("sse:顺序追加;无序重复 id 丢弃(id 守卫)", () => {
    let s = base();
    s = viewerReducer(s, { t: "sse", line: line(5) });
    s = viewerReducer(s, { t: "sse", line: line(6) });
    expect(s.liveLines.map((l) => l.id)).toEqual([5, 6]);
    // 重连回填重复行(<= 尾部 id)→ no-op
    const after = viewerReducer(s, { t: "sse", line: line(6) });
    expect(after).toBe(s);
    const after5 = viewerReducer(s, { t: "sse", line: line(5) });
    expect(after5).toBe(s);
  });

  it("sse:WINDOW 滑动裁剪", () => {
    let s = base();
    for (let id = 1; id <= WINDOW + 25; id++) s = viewerReducer(s, { t: "sse", line: line(id) });
    expect(s.liveLines.length).toBe(WINDOW);
    expect(s.liveLines[0].id).toBe(25 + 1);
  });

  it("fallback:仅视图为空时顶上", () => {
    const page = [line(1), line(2)];
    const empty = base();
    expect(viewerReducer(empty, { t: "fallback", page }).liveLines).toEqual(page);
    const s = viewerReducer(base(), { t: "sse", line: line(9) });
    const after = viewerReducer(s, { t: "fallback", page });
    expect(after).toBe(s); // 已有实时行 → 不回退
  });

  it("following/newCount/clear-new/searching:相等短路(引用不变)", () => {
    const s = base();
    expect(viewerReducer(s, { t: "following", v: true })).toBe(s);
    expect(viewerReducer(s, { t: "searching", v: false })).toBe(s);
    const n0 = viewerReducer(s, { t: "clear-new" });
    expect(n0).toBe(s);
    const n1 = viewerReducer(s, { t: "increment-new" });
    expect(n1.newCount).toBe(1);
    expect(viewerReducer(n1, { t: "clear-new" }).newCount).toBe(0);
    const fol = viewerReducer(s, { t: "following", v: false });
    expect(fol.following).toBe(false);
  });

  it("prepend:MAX_PREFIX 裁剪(保最新侧)", () => {
    let s = base();
    const MAX = 5000;
    // 真实场景:loadMoreAbove 每次取 id<firstId 的最近页,批次与已有前缀相邻(单调)。
    // 0..2999 + 3000..5999 共 6000 条 → 裁剪窗口尾部 5000(丢最旧 0..999)。
    s = viewerReducer(s, { t: "prepend", lines: Array.from({ length: 3000 }, (_, i) => line(3000 + i)) });
    const s2 = viewerReducer(s, { t: "prepend", lines: Array.from({ length: 3000 }, (_, i) => line(i)) });
    expect(s2.historyPrefix.length).toBe(MAX);
    expect(s2.historyPrefix[0].id).toBe(1000);
    expect(s2.historyPrefix[s2.historyPrefix.length - 1].id).toBe(5999);
    // 裁剪后仍单调(id 升序:最旧在前)
    for (let i = 1; i < s2.historyPrefix.length; i += 1) {
      expect(s2.historyPrefix[i].id).toBe(s2.historyPrefix[i - 1].id + 1);
    }
  });

  it("back-live:清历史页/前缀并重置跟随态", () => {
    let s = base();
    s = viewerReducer(s, { t: "history-page", page: [line(3)] });
    s = viewerReducer(s, { t: "prepend", lines: [line(1), line(2)] });
    s = viewerReducer(s, { t: "input" });
    s = viewerReducer(s, { t: "back-live" });
    expect(s.historyPage).toBeNull();
    expect(s.historyPrefix).toEqual([]);
    expect(s.following).toBe(true);
    expect(s.newCount).toBe(0);
  });

  it("search:search-start 清匹配并以 hasSearched 标记;search-done 设首匹配;input 清为未搜态", () => {
    let s = base();
    s = viewerReducer(s, { t: "search-start" });
    expect(s.hasSearched).toBe(true);
    s = viewerReducer(s, { t: "search-done", matches: [11, 22], total: 2 });
    expect(s.matches).toEqual([11, 22]);
    expect(s.matchIdx).toBe(0);
    // 无匹配 → idx -1
    s = viewerReducer(s, { t: "search-done", matches: [], total: 0 });
    expect(s.matchIdx).toBe(-1);
    // input:清匹配 + 未搜态 + searching 熄灯
    s = viewerReducer(s, { t: "searching", v: true });
    s = viewerReducer(s, { t: "input" });
    expect(s.hasSearched).toBe(false);
    expect(s.searching).toBe(false);
    expect(s.matches).toEqual([]);
  });

  it("match-idx 与 scroll-target 直写", () => {
    let s = base();
    s = viewerReducer(s, { t: "match-idx", i: 3 });
    expect(s.matchIdx).toBe(3);
    s = viewerReducer(s, { t: "scroll-target", id: 42 });
    expect(s.scrollTargetId).toBe(42);
  });

  it("at-oldest 直写", () => {
    const s = viewerReducer(base(), { t: "at-oldest", v: true });
    expect(s.atOldest).toBe(true);
  });
});
