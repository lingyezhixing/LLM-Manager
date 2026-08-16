// 模型定义表单纯逻辑:空草稿/深拷贝/深相等/客户端门控/载荷清理(自 model-def-form 拆出)。

import { isPendingKey } from "@/lib/pending-keys";
import { type ModelDef, type SchemeDef } from "@/lib/api";

export const MODES = ["Chat", "Embedding", "Reranker"];

// 空方案(新增/追加方案共用):一个默认 config_source + 空 command。
export function emptyScheme(): SchemeDef {
  return {
    config_source: "default",
    required_devices: [],
    command: { exe: "", args: [], env: {}, cwd: null, conda_env: null },
    memory_mb: {},
  };
}

// 空模型草稿(新增用):一个空 scheme。
export function emptyModel(): ModelDef {
  return {
    name: "",
    mode: "Chat",
    port: 0,
    auto_start: false,
    aliases: [],
    schemes: [emptyScheme()],
    pricing: { pricing_type: "tier", hourly_price: 0, support_cache: false, tiers: [] },
  };
}

// 深拷贝(字段全 JSON 可序列化)。用于隔离 form/baseline 与查询缓存。
export const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x)) as T;

// 草稿 vs baseline 深相等:字段全 JSON 可序列化且键序稳定(均经 clone/cleanPayload 同构构造),
// 故用 JSON.stringify 比较 —— 与 clone 的序列化机制一致,语义统一。
export const deepEqual = (a: ModelDef, b: ModelDef): boolean => JSON.stringify(a) === JSON.stringify(b);

// 客户端门控:明显空缺则禁用保存(M6)。
export function clientValid(form: ModelDef): boolean {
  if (!form.name.trim()) return false;
  if (form.aliases.length === 0 || form.aliases.some((a) => !a.trim())) return false;
  if (form.schemes.length === 0) return false;
  return form.schemes.every(
    (s) => s.config_source.trim() !== "" && s.command.exe.trim() !== "",
  );
}

// 保存前清理:去 args/required_devices 空串、env/memory_mb 空键/待填哨兵(防 argv 传空参、哨兵漏存)。
export function stripEmptyKeys(rec: Record<string, string | number>): Record<string, string | number> {
  const out: Record<string, string | number> = {};
  for (const [k, v] of Object.entries(rec)) {
    if (k.trim() !== "" && !isPendingKey(k)) out[k] = v;
  }
  return out;
}

export function cleanPayload(m: ModelDef): ModelDef {
  return {
    ...m,
    schemes: m.schemes.map((s) => {
      // memory_mb:每个 required_device 必须有条目(缺则补 0)。0 = 该设备仅用于方案匹配、
      // 不占显存预算(调度时空缺与 0 等价,都不检查显存);显式持久化 0 让「所见即所存」,
      // 消除前端显示 0(合并默认)但 DB 存空 {} 的假象。
      const memory_mb: Record<string, number> = {};
      for (const d of s.required_devices) {
        if (d.trim() !== "") memory_mb[d] = s.memory_mb[d] ?? 0;
      }
      for (const [k, v] of Object.entries(s.memory_mb)) {
        if (k.trim() !== "" && !isPendingKey(k) && !(k in memory_mb)) memory_mb[k] = v;   // 保留 required 之外的显存条目
      }
      return {
        ...s,
        required_devices: s.required_devices.filter((d) => d.trim() !== ""),
        command: {
          ...s.command,
          args: s.command.args.filter((a) => a !== ""),
          env: stripEmptyKeys(s.command.env) as Record<string, string>,
        },
        memory_mb,
      };
    }),
  };
}
