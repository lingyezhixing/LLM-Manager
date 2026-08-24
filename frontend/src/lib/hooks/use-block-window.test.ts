import { describe, expect, it } from "vitest";
import { blockOf } from "@/lib/hooks/use-block-window";

describe("blockOf", () => {
  it("边界划分(每 120 行一块)", () => {
    expect(blockOf(0)).toBe(0);
    expect(blockOf(119)).toBe(0);
    expect(blockOf(120)).toBe(1);
    expect(blockOf(239)).toBe(1);
    expect(blockOf(240)).toBe(2);
  });
});
