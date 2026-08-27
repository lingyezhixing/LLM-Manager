// 云服务商表单纯逻辑:空草稿/深拷贝/深相等(与 lib/model-def.ts 同构)。
import { type CloudModel, type ProviderDef } from "@/lib/api";

// 空云端模型(模型区新增共用):峰谷结构初始为空(开关关、无窗口、双阶梯空)。
export function emptyCloudModel(): CloudModel {
  return {
    model_name: "",
    support_cache: false,
    dual_pricing: false,
    offpeak_windows: [],
    tiers_base: [],
    tiers_offpeak: [],
  };
}

// 谷时段窗口的 HH:MM ↔ 当日分钟转换。严格两位分钟;越界/格式错一律 null,
// 调用方忽略该次变更(value 始终由 minutesToHhmm 反推,非法输入进不了状态)。
const HHMM_RE = /^(\d{1,2}):(\d{2})$/;

export function hhmmToMinutes(s: string): number | null {
  const m = HHMM_RE.exec(s.trim());
  if (!m) return null;
  const h = Number(m[1]);
  const min = Number(m[2]);
  if (h > 23 || min > 59) return null;
  return h * 60 + min;
}

export function minutesToHhmm(min: number): string | null {
  if (!Number.isInteger(min) || min < 0 || min > 1439) return null;
  const h = Math.floor(min / 60);
  const mm = min % 60;
  return `${String(h).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
}

// 空服务商草稿(新增用):无模型、无映射。
export function emptyProvider(): ProviderDef {
  return {
    name: "",
    api_key: "",
    enabled: true,
    openai_base: "",
    responses_base: "",
    claude_base: "",
    extra_headers: {},
    models: [],
    mappings: [],
  };
}

// 深拷贝(字段全 JSON 可序列化)。用于隔离 form/baseline 与查询缓存。
export const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x)) as T;

// 草稿 vs baseline 深相等:字段全 JSON 可序列化且键序稳定(均经 clone 同构构造),
// 故用 JSON.stringify 比较 —— 与 clone 的序列化机制一致,语义统一。
export const deepEqual = (a: ProviderDef, b: ProviderDef): boolean => JSON.stringify(a) === JSON.stringify(b);
