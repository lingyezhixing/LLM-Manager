"""把上游字节流解析成"人读的回复文本",并提供输入脱敏。

与 ``data/metering.py`` 同性质:**纯解析、无 IO、绝不抛**。解析失败只记 debug 并返回空增量
——抓取是旁路,绝不允许影响转发。

展示模型是"按到达顺序的纯文本流",不是结构化消息树:不美化 JSON、不合并消息。
工具调用按 index 分开累积、完成时才落块(partial JSON 在流中途不完整,交错拼会搅成一团)。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from llm_manager.gateway.live_requests import MAX_PREVIEW_CHARS

logger = logging.getLogger(__name__)

_DATA_URI = re.compile(r"^data:[\w.+-]+/[\w.+-]+(;[^,]*)?;base64,", re.IGNORECASE)
_B64ISH = re.compile(r"^[A-Za-z0-9+/\r\n=\s]+$")
_BIG_STRING = 32 * 1024
_TOOL_FINISH = frozenset({"tool_calls", "function_call"})
_REASONING_MARK = "\n[思考]\n"


def _human(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def elide_media(value: str) -> str:
    """把内联 base64 / data URI 换成带类型的占位符;远端 URL 与普通文本原样返回。"""
    m = _DATA_URI.match(value)
    if m:
        mime = value[5:].split(";", 1)[0]
        return f"<已省略 {mime} · {_human(len(value))}>"
    if len(value) > _BIG_STRING and _B64ISH.match(value):
        return f"<已省略 base64 · {_human(len(value))}>"
    return value


def _walk(value: object) -> object:
    if isinstance(value, dict):
        out: dict = {}
        for k, v in value.items():
            if k == "b64_json" and isinstance(v, str):
                out[k] = elide_media(v)
            else:
                out[k] = _walk(v)
        return out
    if isinstance(value, list):
        return [_walk(v) for v in value]
    if isinstance(value, str):
        return elide_media(value)
    return value


def sanitize_body(body: dict) -> dict:
    """返回**新结构**:媒体载荷被省略,原 body 不受影响(转发用的那份必须原封不动)。"""
    out = _walk(body)
    return out if isinstance(out, dict) else {}


def _text_parts(content: object) -> str:
    """Anthropic/OpenAI 的 content 数组 → 纯文本(只取 type=text)。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if (
            isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ):
            parts.append(item["text"])
    return "".join(parts)


def extract_preview(body: object) -> str:
    """最后一条 user 消息的文本(Agent 循环里"当前指令"比"最初任务"更能认出跑飞的那条)。

    非 chat 路径回退:``input`` / ``prompt`` 字符串,再不行给压缩 JSON 前 300 字
    —— 预览是列表里唯一能认出"哪条在跑飞"的线索,不能是空。
    """
    if not isinstance(body, dict):
        return ""
    messages = body.get("messages")
    if isinstance(messages, list):
        preview = ""
        for msg in messages:
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue
            text = _text_parts(msg.get("content"))
            preview = text if text else "[非文本内容]"
        if preview:
            return preview[:MAX_PREVIEW_CHARS]
    for key in ("input", "prompt"):
        value = body.get(key)
        if isinstance(value, str) and value:
            return value[:MAX_PREVIEW_CHARS]
    try:
        return json.dumps(body, ensure_ascii=False)[:MAX_PREVIEW_CHARS]
    except Exception:  # noqa: BLE001
        return ""


@dataclass(frozen=True)
class CaptureResult:
    text: str
    finish_reason: str | None = None
    has_reasoning: bool = False
    has_tool_calls: bool = False


def _tool_block(name: str, args: str) -> str:
    return f"\n[tool: {name or 'unknown'}]\n{args}\n"


class StreamTextExtractor:
    """增量提取器。``feed`` 返回本 chunk 新增的**可显示文本**(已含标记)。"""

    def __init__(self, path: str) -> None:
        self._path = path.lstrip("/")
        self._buf = bytearray()
        self._tools: dict[int, dict[str, str]] = {}
        self._reported: set[int] = set()
        self._reasoning_open = False
        self.finish_reason: str | None = None
        self.has_reasoning = False
        self.has_tool_calls = False

    # ---------- 输入 ----------

    def feed(self, chunk: bytes) -> str:
        if chunk:
            self._buf.extend(chunk)
        out: list[str] = []
        while True:
            idx = self._buf.find(b"\n\n")
            if idx < 0:
                break
            block = bytes(self._buf[:idx])
            del self._buf[: idx + 2]
            out.append(self._handle_block(block))
        return "".join(out)

    def flush(self) -> str:
        """流已结束:处理残留半块并输出未落块的工具调用。"""
        out: list[str] = []
        if self._buf:
            block = bytes(self._buf)
            self._buf.clear()
            out.append(self._handle_block(block))
        out.append(self._flush_tools())
        return "".join(out)

    # ---------- 分派 ----------

    def _handle_block(self, block: bytes) -> str:
        try:
            text = block.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return ""
        payloads = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        if not payloads:
            return ""
        out: list[str] = []
        for payload in payloads:
            if not payload or payload == "[DONE]":
                continue
            if self._path not in _KNOWN:
                out.append(self._fallback(payload))
                continue
            try:
                obj = json.loads(payload)
            except Exception:  # noqa: BLE001
                logger.debug("stream capture skipped malformed payload for path=%s", self._path)
                continue
            if not isinstance(obj, dict):
                continue
            try:
                out.append(self._dispatch(obj))
            except Exception:  # noqa: BLE001
                logger.debug("stream capture dispatch failed for path=%s", self._path)
        return "".join(out)

    def _fallback(self, payload: str) -> str:
        return elide_media(payload) + "\n"

    def _dispatch(self, obj: dict) -> str:
        if self._path.startswith("v1/chat/completions"):
            return self._openai_chat(obj)
        if self._path.startswith("v1/completions"):
            return self._openai_completion(obj)
        if self._path.startswith("v1/messages"):
            return self._anthropic(obj)
        if self._path.startswith("v1/responses"):
            return self._responses(obj)
        return ""

    # ---------- 各协议 ----------

    def _openai_chat(self, obj: dict) -> str:
        choices = obj.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        choice = choices[0]
        if not isinstance(choice, dict):
            return ""
        reason = choice.get("finish_reason")
        if isinstance(reason, str):
            self.finish_reason = reason
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            return ""
        out: list[str] = []
        content = delta.get("content")
        if isinstance(content, str):
            out.append(content)
        reasoning = delta.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            self.has_reasoning = True
            out.append(self._think(reasoning))
        for call in delta.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            self.has_tool_calls = True
            idx = call.get("index")
            idx = idx if isinstance(idx, int) else 0
            slot = self._tools.setdefault(idx, {"name": "", "args": ""})
            fn = call.get("function")
            if isinstance(fn, dict):
                if isinstance(fn.get("name"), str):
                    slot["name"] = fn["name"]
                if isinstance(fn.get("arguments"), str):
                    slot["args"] += fn["arguments"]
        if self.finish_reason in _TOOL_FINISH:
            out.append(self._flush_tools())
        return "".join(out)

    def _openai_completion(self, obj: dict) -> str:
        choices = obj.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        choice = choices[0]
        if not isinstance(choice, dict):
            return ""
        reason = choice.get("finish_reason")
        if isinstance(reason, str):
            self.finish_reason = reason
        text = choice.get("text")
        return text if isinstance(text, str) else ""

    def _anthropic(self, obj: dict) -> str:
        kind = obj.get("type")
        if kind == "content_block_delta":
            delta = obj.get("delta")
            if not isinstance(delta, dict):
                return ""
            if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                return delta["text"]
            if delta.get("type") == "thinking_delta" and isinstance(delta.get("thinking"), str):
                self.has_reasoning = True
                return self._think(delta["thinking"])
            if delta.get("type") == "input_json_delta":
                self.has_tool_calls = True
                idx = obj.get("index")
                idx = idx if isinstance(idx, int) else 0
                slot = self._tools.setdefault(idx, {"name": "", "args": ""})
                if isinstance(delta.get("partial_json"), str):
                    slot["args"] += delta["partial_json"]
                return ""
            return ""
        if kind == "content_block_start":
            block = obj.get("content_block")
            if isinstance(block, dict) and block.get("type") == "tool_use":
                self.has_tool_calls = True
                idx = obj.get("index")
                idx = idx if isinstance(idx, int) else 0
                slot = self._tools.setdefault(idx, {"name": "", "args": ""})
                if isinstance(block.get("name"), str):
                    slot["name"] = block["name"]
            return ""
        if kind == "content_block_stop":
            idx = obj.get("index")
            idx = idx if isinstance(idx, int) else 0
            return self._flush_tools(only=idx)
        if kind == "message_delta":
            delta = obj.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("stop_reason"), str):
                self.finish_reason = delta["stop_reason"]
            return ""
        return ""

    def _responses(self, obj: dict) -> str:
        kind = obj.get("type")
        if kind == "response.output_text.delta":
            delta = obj.get("delta")
            return delta if isinstance(delta, str) else ""
        if kind == "response.reasoning_summary_text.delta":
            delta = obj.get("delta")
            if isinstance(delta, str) and delta:
                self.has_reasoning = True
                return self._think(delta)
            return ""
        if kind == "response.function_call_arguments.delta":
            self.has_tool_calls = True
            idx = obj.get("output_index")
            idx = idx if isinstance(idx, int) else 0
            slot = self._tools.setdefault(idx, {"name": "", "args": ""})
            if isinstance(obj.get("delta"), str):
                slot["args"] += obj["delta"]
            return ""
        if kind in ("response.completed", "response.incomplete"):
            resp = obj.get("response")
            if isinstance(resp, dict):
                details = resp.get("incomplete_details")
                if isinstance(details, dict) and isinstance(details.get("reason"), str):
                    self.finish_reason = details["reason"]
                elif isinstance(resp.get("status"), str):
                    self.finish_reason = resp["status"]
            return ""
        return ""

    # ---------- 工具块与思考标记 ----------

    def _think(self, text: str) -> str:
        if self._reasoning_open:
            return text
        self._reasoning_open = True
        return _REASONING_MARK + text

    def _flush_tools(self, only: int | None = None) -> str:
        out: list[str] = []
        for idx in sorted(self._tools):
            if only is not None and idx != only:
                continue
            if idx in self._reported:
                continue
            self._reported.add(idx)
            slot = self._tools[idx]
            out.append(_tool_block(slot.get("name", ""), slot.get("args", "")))
        return "".join(out)


_KNOWN = frozenset({"v1/chat/completions", "v1/completions", "v1/messages", "v1/responses"})


def extract_final_text(path: str, body: bytes) -> CaptureResult:
    """非流式:从完整响应体提取展示文本。"""
    p = path.lstrip("/")
    try:
        obj = json.loads(body)
    except Exception:  # noqa: BLE001
        return CaptureResult(text=elide_media(body.decode("utf-8", "replace")))
    if not isinstance(obj, dict):
        return CaptureResult(text=json.dumps(obj, ensure_ascii=False, indent=2)[:4096])

    if p.startswith("v1/chat/completions"):
        choices = obj.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            choice = choices[0]
            msg = choice.get("message")
            parts: list[str] = []
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str):
                    parts.append(content)
                elif isinstance(content, list):
                    parts.append(_text_parts(content))
                reasoning = msg.get("reasoning_content")
                if isinstance(reasoning, str) and reasoning:
                    parts.append(_REASONING_MARK + reasoning)
                for call in msg.get("tool_calls") or []:
                    if isinstance(call, dict) and isinstance(call.get("function"), dict):
                        fn = call["function"]
                        parts.append(
                            _tool_block(
                                str(fn.get("name") or ""),
                                str(fn.get("arguments") or ""),
                            )
                        )
            reason = choice.get("finish_reason")
            return CaptureResult(
                text="".join(parts),
                finish_reason=reason if isinstance(reason, str) else None,
                has_reasoning=isinstance(msg, dict)
                and isinstance(msg.get("reasoning_content"), str),
                has_tool_calls=bool(isinstance(msg, dict) and msg.get("tool_calls")),
            )

    if p.startswith("v1/completions"):
        choices = obj.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            reason = choices[0].get("finish_reason")
            text = choices[0].get("text")
            return CaptureResult(
                text=text if isinstance(text, str) else "",
                finish_reason=reason if isinstance(reason, str) else None,
            )

    if p.startswith("v1/messages"):
        parts: list[str] = []
        has_reasoning = False
        has_tools = False
        content = obj.get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif item.get("type") == "thinking" and isinstance(item.get("thinking"), str):
                    has_reasoning = True
                    parts.append(_REASONING_MARK + item["thinking"])
                elif item.get("type") == "tool_use":
                    has_tools = True
                    parts.append(
                        _tool_block(
                            str(item.get("name") or ""),
                            json.dumps(item.get("input"), ensure_ascii=False),
                        )
                    )
        reason = obj.get("stop_reason")
        return CaptureResult(
            text="".join(parts),
            finish_reason=reason if isinstance(reason, str) else None,
            has_reasoning=has_reasoning,
            has_tool_calls=has_tools,
        )

    if p.startswith("v1/responses"):
        text = obj.get("output_text")
        if not isinstance(text, str):
            parts: list[str] = []
            output = obj.get("output")
            if isinstance(output, list):
                for item in output:
                    if isinstance(item, dict) and isinstance(item.get("content"), list):
                        parts.append(_text_parts(item["content"]))
            text = "".join(parts)
        status = obj.get("status")
        return CaptureResult(text=text, finish_reason=status if isinstance(status, str) else None)

    return CaptureResult(text=json.dumps(sanitize_body(obj), ensure_ascii=False, indent=2)[:4096])
