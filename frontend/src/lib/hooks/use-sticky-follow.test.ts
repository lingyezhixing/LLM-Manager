import { describe, expect, it } from "vitest";
import { STICKY_THRESHOLD, isAtBottom } from "@/lib/hooks/use-sticky-follow";

const el = (scrollHeight: number, scrollTop: number, clientHeight: number) =>
  ({ scrollHeight, scrollTop, clientHeight });

describe("isAtBottom", () => {
  it("默认阈值 24px,gap < 阈值视为贴底", () => {
    expect(STICKY_THRESHOLD).toBe(24);
    expect(isAtBottom(el(1000, 800, 200))).toBe(true);   // gap 0(正好贴底)
    expect(isAtBottom(el(1000, 780, 200))).toBe(true);   // gap 20
  });

  it("gap >= 阈值不贴底(严格小于)", () => {
    expect(isAtBottom(el(1000, 776, 200))).toBe(false);  // gap 24
    expect(isAtBottom(el(1000, 0, 200))).toBe(false);    // 在顶部
  });

  it("无溢出(gap 0)恒贴底;自定义阈值", () => {
    expect(isAtBottom(el(100, 0, 300))).toBe(true);
    expect(isAtBottom(el(100, 76, 20), 8)).toBe(true);   // gap 4 < 8
    expect(isAtBottom(el(100, 72, 20), 8)).toBe(false);  // gap 8
  });
});
