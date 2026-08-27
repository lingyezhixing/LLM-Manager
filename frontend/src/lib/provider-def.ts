// 云服务商表单纯逻辑:空草稿/深拷贝/深相等(与 lib/model-def.ts 同构)。
import { type CloudModel, type ProviderDef } from "@/lib/api";

// 空云端模型(模型区新增共用):峰谷结构先建为空,UI 暂不提供双价编辑。
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
