// 模型 CRUD 422 有三种 detail 形态:config.validate 的 list[str]、ValueError 的 str、
// Pydantic 字段错的 list[{loc,msg,...}];409 detail 为 str。统一解析为一句可读消息。
export async function parseApiError(res: Response): Promise<Error> {
  let msg = `请求失败: ${res.status}`;
  try {
    const body = await res.json() as { detail?: unknown };
    const d = body?.detail;
    if (Array.isArray(d)) {
      msg = d
        .map((x) => (typeof x === "string" ? x : (x as { msg?: string })?.msg ?? JSON.stringify(x)))
        .join("; ");
    } else if (typeof d === "string") {
      msg = d;
    } else if (typeof body === "string") {
      msg = body;
    }
  } catch {
    // 非 JSON 响应:保留 status
  }
  return new Error(msg);
}

/** 统一 fetch + 解析 JSON:失败抛 parseApiError 解出的可读错误(含后端 detail),
 *  而非裸 "URL failed: ${status}"。GET 直接传 url;POST/PUT/DELETE 经 init 传 body。
 *  本层所有读取端应走此 helper,保证错误信息精度一致(见 usage/logs/config-GET)。 */
export async function apiJson<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const res = await fetch(input, init);
  if (!res.ok) throw await parseApiError(res);
  return (await res.json()) as T;
}
