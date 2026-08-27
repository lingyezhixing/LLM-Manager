import { describe, expect, it } from "vitest";
import { qsForParams, type UsageSeriesParams } from "./usage";

describe("usage source 参数", () => {
  it("默认不含 source(向后兼容)", () => {
    const params: UsageSeriesParams = { period: "7d" };
    expect(qsForParams(params)).toBe("period=7d");
  });

  it("携带 source 时并入查询串", () => {
    const params: UsageSeriesParams = { period: "7d", source: "cloud" };
    expect(qsForParams(params)).toBe("period=7d&source=cloud");
  });

  it("自定义区间 + source 合并", () => {
    const params: UsageSeriesParams = { start: 1, end: 2, source: "local" };
    expect(qsForParams(params)).toBe("start=1&end=2&source=local");
  });
});
