import { useEffect, useRef, useState } from "react";
import { LogPane } from "@/components/logs/log-pane";
import { cancelRequest, type RequestRecord } from "@/lib/api";
import { errMsg, fmtDuration, formatTokens } from "@/lib/format";
import { useConfirm } from "@/lib/hooks/use-confirm";
import { useNowTick } from "@/lib/hooks/use-now-tick";
import { useRequestDetail } from "@/lib/hooks/use-request-detail";
import { useToast } from "@/lib/hooks/use-toast";

const STICKY_THRESHOLD = 24; // 贴底判定(px),与日志页同值

const STATUS_TEXT: Record<RequestRecord["status"], string> = {
  running: "生成中",
  cancelling: "取消中",
  completed: "完成",
  cancelled: "已取消",
  client_closed: "客户端断开",
  failed: "失败",
};

const STATUS_CLS: Record<RequestRecord["status"], string> = {
  running: "text-success border-success/40",
  cancelling: "text-warning border-warning/40",
  completed: "text-muted-foreground border-border",
  cancelled: "text-destructive border-destructive/40",
  client_closed: "text-warning border-warning/40",
  failed: "text-destructive border-destructive/40",
};

function contentText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  const parts = content
    .filter((p): p is { type?: string; text?: string } => !!p && typeof p === "object")
    .filter((p) => p.type === "text" && typeof p.text === "string")
    .map((p) => p.text as string);
  return parts.length > 0 ? parts.join("") : "[非文本内容]";
}

/** 输入区文本:chat 路径按 role 分行,其他路径 pretty JSON。 */
function renderInputText(input: unknown, inputText: string | null): string {
  if (input && typeof input === "object") {
    const messages = (input as { messages?: unknown }).messages;
    if (Array.isArray(messages)) {
      return messages
        .filter((m): m is { role?: string; content?: unknown } => !!m && typeof m === "object")
        .map((m) => `${m.role ?? "?"}: ${contentText(m.content)}`)
        .join("\n");
    }
    return JSON.stringify(input, null, 2);
  }
  return inputText ?? "";
}

/** 输入区摘要:消息条数 + temperature(chat);其他路径只标 JSON。 */
function inputSummary(input: unknown): string {
  if (input && typeof input === "object") {
    const obj = input as { messages?: unknown; temperature?: unknown };
    if (Array.isArray(obj.messages)) {
      const parts = [`${obj.messages.length} 条消息`];
      if (typeof obj.temperature === "number") parts.push(`temperature ${obj.temperature}`);
      return parts.join(" · ");
    }
    return "JSON";
  }
  return "原始内容";
}

/** 单条请求详情:输入(折叠/内部滚动) + 实时输出(分块 + 跟随) + 取消。 */
export function RequestDetail({ id, record }: { id: string; record: RequestRecord | null }) {
  const detail = useRequestDetail(id);
  const r = record ?? detail.record;
  const now = useNowTick(1000);
  const confirm = useConfirm();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [showJump, setShowJump] = useState(false);
  const scroller = useRef<HTMLPreElement | null>(null);
  const following = useRef(true);

  const text = detail.blocks.join("");

  // 切请求:输入区回到折叠态。
  useEffect(() => {
    setExpanded(false);
  }, [id]);

  // 贴底时跟随新内容;上滚即暂停(不把用户拽回去)。
  useEffect(() => {
    const el = scroller.current;
    if (el && following.current) el.scrollTop = el.scrollHeight;
  }, [detail.blocks]);

  if (!r) {
    return (
      <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-border text-xs text-muted-foreground">
        选择左侧请求查看详情
      </div>
    );
  }

  const end = r.finished_at ?? now / 1000;
  const elapsed = fmtDuration(Math.max(0, Math.round(end - r.started_at)));
  const tokens = r.usage ? `${formatTokens(r.usage.output_tokens)} tok` : null;
  const inputText = renderInputText(detail.input, detail.inputText);

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < STICKY_THRESHOLD;
    following.current = atBottom;
    setShowJump(!atBottom);
  };

  const jumpToBottom = () => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
    following.current = true;
    setShowJump(false);
  };

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("已复制当前窗口");
    } catch {
      toast.error("复制失败");
    }
  };

  const onCancel = async () => {
    const ok = await confirm({
      title: "取消这条请求",
      description:
        "会同时断开客户端与上游连接，模型进程保持运行，其他请求不受影响。已生成的部分内容保留在记录里。",
      confirmText: "取消请求",
      cancelText: "算了",
    });
    if (!ok) return;
    setBusy(true);
    try {
      await cancelRequest(id);
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <LogPane
      header={
        <div className="flex w-full items-center gap-2">
          <span className="font-mono text-card-title font-semibold text-foreground">{r.id}</span>
          <span className="truncate text-dense text-muted-foreground">
            {r.path} · {elapsed}
            {tokens ? ` · ${tokens}` : ""}
            {r.finish_reason ? (
              <span className={r.finish_reason === "length" ? " text-warning" : ""}>
                {" "}
                · {r.finish_reason}
              </span>
            ) : null}
            {r.http_status != null && r.http_status >= 400 ? (
              <span className=" text-destructive"> · HTTP {r.http_status}</span>
            ) : null}
          </span>
          <span className="ml-auto flex shrink-0 items-center gap-1.5">
            <span className={`rounded-full border px-2 py-0.5 text-ui transition-colors duration-(--motion-base) ${STATUS_CLS[r.status]}`}>
              {STATUS_TEXT[r.status]}
            </span>
            <button
              type="button"
              onClick={onCopy}
              className="rounded-md border border-border px-2.5 py-1 text-ui text-muted-foreground hover:text-foreground"
            >
              复制
            </button>
            {r.status === "running" && (
              <button
                type="button"
                disabled={busy}
                onClick={onCancel}
                className="rounded-md border border-destructive bg-destructive/10 px-2.5 py-1 text-ui font-medium text-destructive hover:bg-destructive/20 disabled:opacity-60"
              >
                {busy ? "取消中…" : "取消请求"}
              </button>
            )}
          </span>
        </div>
      }
    >
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="shrink-0 border-b border-border-subtle">
          <div className="flex items-center gap-2 px-3 py-1.5 text-dense text-muted-foreground">
            <span>输入</span>
            <span className="truncate">{inputSummary(detail.input)}</span>
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="ml-auto rounded border border-primary-accent/40 px-2 py-0.5 text-ui text-primary-accent hover:bg-primary-accent/10"
            >
              {expanded ? "收起" : "展开"}
            </button>
          </div>
          {expanded ? (
            <pre className="max-h-[40vh] overflow-auto whitespace-pre-wrap break-words px-3 pb-2 font-mono text-ui leading-relaxed text-foreground/85">
              {inputText || "(无输入)"}
            </pre>
          ) : (
            <div className="truncate px-3 pb-1.5 font-mono text-ui text-foreground/70">
              {r.request_preview || "(无输入预览)"}
            </div>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2 border-b border-border-subtle px-3 py-1.5 text-dense text-muted-foreground">
          <span>实时输出</span>
          {r.status === "running" && (
            <span className="flex items-center gap-1 text-success">
              <span className="size-[5px] animate-dot-pulse rounded-full bg-success" />
              正在接收
            </span>
          )}
          {detail.truncated && <span className="ml-auto">前部已折叠</span>}
        </div>

        <div className="relative min-h-0 flex-1">
          <pre
            ref={scroller}
            onScroll={onScroll}
            className="h-full overflow-auto whitespace-pre-wrap break-words p-3 font-mono text-ui leading-relaxed text-foreground/90"
          >
            {detail.blocks.length > 0 ? (
              detail.blocks.map((block, i) => <span key={i}>{block}</span>)
            ) : r.status === "running" ? (
              "等待输出…"
            ) : (
              "无输出"
            )}
          </pre>
          {showJump && (
            <button
              type="button"
              onClick={jumpToBottom}
              className="absolute bottom-3 right-3 rounded-full border border-primary-accent/50 bg-primary-accent/20 px-2.5 py-1 text-dense text-foreground hover:bg-primary-accent/30"
            >
              回到底部 ↓
            </button>
          )}
        </div>
      </div>
    </LogPane>
  );
}
