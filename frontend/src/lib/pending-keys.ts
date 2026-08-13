/** KeyValueEditor 待填空行的哨兵键:Record 模型下多行待填可共存(用 "" 会互相覆盖)。
 * 键以 PENDING_PREFIX 开头;保存路径(cleanPayload/stripEmptyKeys)须一并剥离。 */
export const PENDING_KEY_PREFIX = "__new__";

export function isPendingKey(k: string): boolean {
  return k.startsWith(PENDING_KEY_PREFIX);
}
