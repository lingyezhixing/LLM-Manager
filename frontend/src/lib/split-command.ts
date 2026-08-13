// 命令行 ↔ argv 解析。前端单行「命令行」输入 → exe + args[]。
//
// 设计要点:
// - 只在引号(" 或 ')内保留空白;`\"` / `\'` 转义字面引号(如 JSON 参数
//   --chat-template-kwargs {\"enable_thinking\":false});其余反斜杠不转义 →
//   Windows 路径(C:\models\glm)的反斜杠原样保留,不会被当转义吃掉。
// - 因此对「带空格的路径」要求用户用引号括起,与终端一致。
// - 拆分器不必完美:args 列表始终可手改(高级区),作为解析歧义的安全回退。
// - 程序仍以 shell=False 跑拆出的 argv,kill_tree / 跨平台 / conda 包装保障不变。

interface ParsedCommand {
  exe: string;
  args: string[];
}

/** 单行命令 → { exe, args }。首 token=exe,其余=args;引号成对,
 * `\"`/`\'` 转义字面引号(引号内外的转义统一处理,其余反斜杠原样)。 */
export function splitCommandLine(line: string): ParsedCommand {
  const tokens: string[] = [];
  let cur = "";
  let quote: '"' | "'" | null = null;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === "\\" && (line[i + 1] === '"' || line[i + 1] === "'")) {
      cur += line[i + 1];   // 转义引号 → 字面加入,不切换引号态
      i++;
    } else if (quote) {
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

/** exe + args → 单行命令。含空白的 token 用双引号括起;token 内的引号转义为 `\"`/`\'`,
 * 「反斜杠+引号」补转义反斜杠,保证与 splitCommandLine 可往返解析(路径反斜杠原样)。 */
export function joinCommandLine(exe: string, args: string[]): string {
  const fmt = (t: string) => {
    let out = "";
    for (let i = 0; i < t.length; i++) {
      const ch = t[i];
      if (ch === '"' || ch === "'") out += "\\" + ch;
      else if (ch === "\\" && (t[i + 1] === '"' || t[i + 1] === "'")) out += "\\\\";
      else out += ch;
    }
    return /\s/.test(t) ? `"${out}"` : out;
  };
  return [exe, ...args]
    .map(fmt)
    .filter((t) => t.length > 0)
    .join(" ");
}

/** 启动命令变量替换:{{port}}/{{alias}} → 模型实际值(与后端 substitute_vars 同占位符;
 * 有占位符才换,无则原样)。双大括号避免与 JSON 参数(单大括号)混淆。 */
function applyVars(text: string, vars?: Record<string, string>): string {
  if (!vars) return text;
  let out = text;
  for (const [k, v] of Object.entries(vars)) out = out.replaceAll(k, v);
  return out;
}

/** 命令预览:最终执行的命令文本(含 conda 包装;不含系统 env 合并、Windows 的 cmd /c 前缀)。
 * vars({{port}}/{{alias}})替换后显示,与后端实际执行一致。 */
export function previewCommand(c: {
  exe: string;
  args: string[];
  conda_env: string | null;
}, vars?: Record<string, string>): string {
  const subst = (t: string) => applyVars(t, vars);
  const base = [c.exe, ...c.args].filter((s) => s.trim() !== "").map(subst).join(" ");
  if (!base) return "";
  return c.conda_env
    ? `conda run -n ${c.conda_env} --no-capture-output ${base}`
    : base;
}

/** 命令行是否存在未闭合的引号(扫到末尾仍处在引号内)。用于 CommandEditor 提示用户:
 *  未闭合时含空格的参数会被错误切分(可在「高级」区手改 args 作回退)。与 splitCommandLine
 *  同源规则(成对引号 + `\"`/`\'` 转义不切换引号态),不重复解析逻辑而是独立轻量扫描。 */
export function hasUnterminatedQuote(line: string): boolean {
  let quote: '"' | "'" | null = null;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === "\\" && (line[i + 1] === '"' || line[i + 1] === "'")) { i++; continue; }
    if (quote) {
      if (ch === quote) quote = null;
    } else if (ch === '"' || ch === "'") {
      quote = ch;
    }
  }
  return quote !== null;
}
