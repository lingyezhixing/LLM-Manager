// Model types hand-defined (codegen regen pending — match gateway/api/models.py ModelInfo;
// re-run `npm run gen:api` with the backend up to regenerate src/api/types.ts).
export interface ModelInfo {
  alias: string;
  mode: string;
  port: number;
  auto_start: boolean;
  status: string;
  pid: number | null;
  pending: number;
  failure_reason: string | null;
  started_at: number | null;   // wall-clock epoch when entered ROUTING (null if not routing)
  last_access: number;         // wall-clock epoch of last activity (0 if never)
}
export interface ModelsResponse { data: ModelInfo[]; }

export async function fetchModels(): Promise<ModelsResponse> {
  const res = await fetch("/api/models");
  if (!res.ok) throw new Error(`/api/models failed: ${res.status}`);
  return (await res.json()) as ModelsResponse;
}

// Device + session types hand-defined (match gateway/api/devices.py + usage.py).
export interface DeviceInfo {
  device_name: string;
  device_type: string;
  memory_type: string;
  total_memory_mb: number;
  available_memory_mb: number;
  used_memory_mb: number;
  usage_percentage: number;
  temperature_celsius: number | null;
}
export interface DevicesResponse { data: DeviceInfo[]; }

export interface SessionUsage {
  started_at: number;       // process start (wall-clock epoch seconds) — frontend ticks uptime
  input_tokens: number;
  output_tokens: number;
  cache_hit: number;
  cache_miss: number;
  hit_rate: number;
}

export async function fetchSessionUsage(): Promise<SessionUsage> {
  const res = await fetch("/api/usage/session");
  if (!res.ok) throw new Error(`/api/usage/session failed: ${res.status}`);
  return (await res.json()) as SessionUsage;
}
