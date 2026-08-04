import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

// 项目首套表单原子。语义 token + --ring focus,二主题自适应。后续 wol/claude/logs/model-def 复用。
// 共享框样式(不含高度):input 原子加 h-9,TextArea 加 min-h + py。
const fieldBase =
  "w-full bg-input border border-border rounded-md px-3 text-sm text-foreground placeholder:text-muted-foreground disabled:opacity-50";
const inputBase = `${fieldBase} h-9`;

export function Field({
  label, hint, error, htmlFor, className, children,
}: {
  label: string;
  hint?: string;
  error?: string | null;
  htmlFor?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={`mb-4 ${className ?? ""}`}>
      <label htmlFor={htmlFor} className="mb-1 block text-xs font-medium text-muted-foreground">
        {label}
      </label>
      {children}
      {hint && !error && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
      {error && <p className="mt-1 text-xs text-destructive">{error}</p>}
    </div>
  );
}

export function TextInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${inputBase} ${props.className ?? ""}`} />;
}

export function NumberInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input type="number" {...props} className={`${inputBase} ${props.className ?? ""}`} />;
}

export function Select({ children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...props} className={`${inputBase} ${props.className ?? ""}`}>
      {children}
    </select>
  );
}

export function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={`${fieldBase} min-h-24 py-2 font-mono ${props.className ?? ""}`} />;
}

// 开关(自启动等布尔项):track + 滑块。开=primary,关=muted;滑块色随态切换保二主题对比。
// role=switch + aria-checked;label 经 htmlFor 关联(点标签即切换)。
export function Switch({
  checked,
  onChange,
  id,
  disabled,
  className,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  id?: string;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      id={id}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${
        checked ? "bg-primary" : "bg-muted"
      } ${className ?? ""}`}
    >
      <span
        className={`inline-block h-5 w-5 rounded-full transition ${
          checked ? "translate-x-5 bg-primary-foreground" : "translate-x-0.5 bg-foreground"
        }`}
      />
    </button>
  );
}
