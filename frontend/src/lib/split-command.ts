// 命令行 ↔ argv 解析。前端单行「命令行」输入 → exe + args[]。
//
// 设计要点:
// - 只在引号(" 或 ')内保留空白;不处理反斜杠转义 → Windows 路径(C:\models\glm)
//   的反斜杠原样保留,不会被当转义吃掉。
// - 因此对「带空格的路径」要求用户用引号括起,与终端一致。
// - 拆分器不必完美:args 列表始终可手改(高级区),作为解析歧义的安全回退。
// - 程序仍以 shell=False 跑拆出的 argv,kill_tree / 跨平台 / conda 包装保障不变。

interface ParsedCommand {
  exe: string;
  args: string[];
}

/** 单行命令 → { exe, args }。首 token=exe,其余=args;引号成对,不转义反斜杠。 */
export function splitCommandLine(line: string): ParsedCommand {
  const tokens: string[] = [];
  let cur = "";
  let quote: '"' | "'" | null = null;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quote) {
      if (ch === quote) quote = null;
      else cur += ch;
    } else if (ch === '"' || ch === "'") {
      quote = ch;
    } else if (ch === " " || ch === "\t" || ch === "\n" || ch === "\r") {
      if (cur !== "") {
        tokens.push(cur);
        cur = "";
      }
    } else {
      cur += ch;
    }
  }
  if (cur !== "") tokens.push(cur);
  const [exe, ...args] = tokens;
  return { exe: exe ?? "", args };
}

/** exe + args → 单行命令。含空白的 token 用双引号括起以保证可往返解析。 */
export function joinCommandLine(exe: string, args: string[]): string {
  const fmt = (t: string) => (/\s/.test(t) ? `"${t}"` : t);
  return [exe, ...args]
    .map(fmt)
    .filter((t) => t.length > 0)
    .join(" ");
}

/** 预览最终将执行的命令(含 conda 包装;不含系统 env 合并、Windows 的 cmd /c 前缀)。 */
export function previewCommand(c: {
  exe: string;
  args: string[];
  conda_env: string | null;
}): string {
  const base = [c.exe, ...c.args].filter((s) => s.trim() !== "").join(" ");
  if (!base) return "";
  return c.conda_env
    ? `conda run -n ${c.conda_env} --no-capture-output ${base}`
    : base;
}
