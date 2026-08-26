// Claude 预设 env JSON 解析/相等判定(纯函数)。

import { errMsg } from "@/lib/format";

// 解析预设 JSON 文本:须为 str→str 对象。失败 → { ok:false, message }。
export function parseEnvJson(text: string): { ok: true; value: Record<string, string> } | { ok: false; message: string } {
  try {
    const v: unknown = JSON.parse(text);
    if (typeof v !== "object" || v === null || Array.isArray(v)) {
      return { ok: false, message: "JSON 须为对象,如 {\"ANTHROPIC_BASE_URL\": \"http://...\"}" };
    }
    for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
      if (typeof val !== "string") return { ok: false, message: `键「${k}」的值须为字符串` };
    }
    return { ok: true, value: v as Record<string, string> };
  } catch (e) {
    return { ok: false, message: `JSON 格式错误:${errMsg(e)}` };
  }
}

// JSON 语义相等(忽略格式差异):useSyncedForm 的 dirty/follow 判定用。保存成功后
// config refetch 会把服务端预设按 2 空格重序列化回传——若按字符串比较,会在刚保存完
// 就把用户手写格式覆盖成服务端格式(回归);语义相等则不动文本。
export function jsonEq(a: string, b: string): boolean {
  const pa = parseEnvJson(a);
  const pb = parseEnvJson(b);
  if (pa.ok && pb.ok) return JSON.stringify(pa.value) === JSON.stringify(pb.value);
  return a === b;
}
