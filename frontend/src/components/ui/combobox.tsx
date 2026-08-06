import { useEffect, useRef, useState } from "react";
import { TextInput } from "@/components/ui/form";

// 输入框 + 下拉建议:聚焦即展开全部选项(输入时按包含过滤),点击选项填入,也可自由输入。
// 与原生 datalist 的区别:input 已有值时仍显示全部选项(datalist 只显示匹配当前值的,
// 导致已填值看不到其他可选项)。onMouseDown + preventDefault 让选项点击先于 input blur。
export function ComboboxInput({
  value,
  onChange,
  options,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocDown);
    return () => document.removeEventListener("mousedown", onDocDown);
  }, [open]);

  const q = filter.trim().toLowerCase();
  const shown = q === "" ? options : options.filter((o) => o.toLowerCase().includes(q));

  return (
    <div ref={boxRef} className="relative min-w-0 flex-1">
      <TextInput
        value={value}
        placeholder={placeholder}
        onFocus={() => { setFilter(""); setOpen(true); }}
        onChange={(e) => { setFilter(e.target.value); setOpen(true); onChange(e.target.value); }}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
          if (e.key === "Enter") { e.preventDefault(); setOpen(false); }
        }}
      />
      {open && shown.length > 0 && (
        <div className="absolute z-20 mt-1 max-h-48 w-full overflow-auto rounded-md border border-border bg-popover py-1 text-popover-foreground shadow-lg">
          {shown.map((o) => (
            <button
              key={o}
              type="button"
              onMouseDown={(e) => { e.preventDefault(); onChange(o); setOpen(false); }}
              className="block w-full px-3 py-1.5 text-left text-sm hover:bg-card-hover"
            >
              {o}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
