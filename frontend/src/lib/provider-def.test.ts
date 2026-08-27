import { describe, expect, it } from "vitest";

import { emptyCloudModel, hhmmToMinutes, minutesToHhmm } from "./provider-def";

describe("时段 HH:MM ↔ 当日分钟", () => {
  it("合法值双向转换", () => {
    expect(hhmmToMinutes("23:00")).toBe(1380);
    expect(hhmmToMinutes("0:05")).toBe(5);
    expect(minutesToHhmm(1380)).toBe("23:00");
    expect(minutesToHhmm(5)).toBe("00:05");
    expect(minutesToHhmm(1439)).toBe("23:59");
  });

  it("非法输入返回 null(调用方忽略变更)", () => {
    expect(hhmmToMinutes("24:00")).toBeNull(); // 时越界
    expect(hhmmToMinutes("10:60")).toBeNull(); // 分越界
    expect(hhmmToMinutes("10:5")).toBeNull(); // 分钟必须两位
    expect(hhmmToMinutes("")).toBeNull();
    expect(hhmmToMinutes("abc")).toBeNull();
    // 越界分钟也输出 null:防御后端约束之外的非法态
    expect(minutesToHhmm(1440)).toBeNull();
    expect(minutesToHhmm(-1)).toBeNull();
  });
});

describe("emptyCloudModel", () => {
  it("峰谷结构初始为空(开关关、无窗口、双阶梯空)", () => {
    const m = emptyCloudModel();
    expect(m.dual_pricing).toBe(false);
    expect(m.peak_windows).toEqual([]);
    expect(m.tiers_peak).toEqual([]);
  });
});
