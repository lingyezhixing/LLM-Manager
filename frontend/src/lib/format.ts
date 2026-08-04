/** 通用数值/时长/金额格式化(用量页/模型卡/日志页/计费共用)。
 *  K/M 后缀令牌数;ms/s 延迟;% 命中率/占比;¥ 金额;时长/时间串。 */

/** 令牌数格式化(K/M 后缀)。decimals 同时作用于 M 与 K 分支(统一旧版 2/1/0 位三档);调用方按展示空间传参,默认 1 位。 */
export function formatTokens(n: number, decimals = 1): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(decimals)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(decimals)}K`;
  return `${n}`;
}

/** 秒数 → 精确空闲时长(小时起):45s / 2m 5s / 1h 2m 30s。 */
export function formatIdle(sec: number): string {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

export function formatCount(n: number): string {
  return n.toLocaleString("en-US");
}

export function formatHitRate(r: number): string {
  return `${(r * 100).toFixed(1)}%`;
}

export function formatPercent(share: number): string {
  return `${(share * 100).toFixed(0)}%`;
}

export function formatLatency(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** 金额(元)格式化 — 计费页面用。 */
export function formatCost(yuan: number): string {
  if (yuan <= 0) return "¥0";
  if (yuan < 0.01) return "<¥0.01";
  if (yuan >= 1) return `¥${yuan.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
  return `¥${yuan.toFixed(2)}`;
}

/** 时间戳(epoch 秒)→ 本地时间字符串(24 小时制)— 日志查看页会话列表用。 */
export function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
}

/** 秒数 → 人类可读时长(如 3m 12s、1h 5m)— 日志查看页会话列表用。 */
export function fmtDuration(sec: number): string {
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;
  const h = Math.floor(sec / 3600);
  return `${h}h ${Math.round((sec % 3600) / 60)}m`;
}

/** 数字输入框值解析:空串 → 0(NumberInput 受控值常见)。 */
export function numFromStr(s: string): number {
  return s === "" ? 0 : Number(s);
}

/** 同上,但空串 → null(可空数值字段,如 pricing tier 区间上界 max)。 */
export function numOrNull(s: string): number | null {
  return s === "" ? null : Number(s);
}
