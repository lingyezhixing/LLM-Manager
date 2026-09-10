"""按路径索引的 token 解析器 + 共享用量指标(hit_rate)。纯字典注册表 + @_safe
总体异常安全(在 stream-finally 中抛异常会截断客户端流)。

未知路径默认回退 parse_generic(保守按字段分类,非顺序盲试;仅无歧义信号
返回非零,宁可漏计也不误记)。三大 API 显式注册;此外 parse_generic 还识别
Ollama(prompt_eval_count/eval_count)、Gemini(usageMetadata)、Cohere
(meta.billed_units)等常见约定,任意转发路径的用量尽力计数。

另含共享用量指标 helper(hit_rate),供 usage(计费/会话计数)引用。"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cache_tokens: int
    prompt_tokens: int


def _safe(parser: Callable[[bytes], TokenUsage]) -> Callable[[bytes], TokenUsage]:
    def wrapped(body: bytes) -> TokenUsage:
        try:
            return parser(body)
        except Exception as e:  # noqa: BLE001
            logger.debug("[parser] %s failed: %s", parser.__name__, e)
            return TokenUsage(0, 0, 0, 0)

    wrapped.__name__ = parser.__name__
    return wrapped


def hit_rate(hits: int, misses: int) -> float:
    """缓存命中率:hits / (hits + misses);无分母 → 0.0。"""
    denom = hits + misses
    return hits / denom if denom else 0.0


def _body_str(body: bytes) -> str:
    return body.decode("utf-8", errors="replace")


def _is_sse(s: str) -> bool:
    for line in s.splitlines():
        ls = line.lstrip()
        if ls.startswith(("data:", "event:")):
            return True
    return False


def iter_blocks(s: str):
    """逐个产出 'data: <payload>' payload 字符串(跳过 [DONE])。"""
    for line in s.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            payload = line[6:].strip()
            if payload and payload != "[DONE]":
                yield payload


def _try_json(s: str):
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None


def _to_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


@_safe
def parse_openai(body: bytes) -> TokenUsage:
    s = _body_str(body)
    if _is_sse(s):
        blocks = list(reversed(list(iter_blocks(s))))
    else:
        obj = _try_json(s)
        blocks = [json.dumps(obj)] if obj is not None else []
    for blk in blocks:
        d = _try_json(blk)
        if not isinstance(d, dict):
            continue
        t = d.get("timings")
        if isinstance(t, dict):
            cache_n = _to_int(t.get("cache_n"))
            prompt_n = _to_int(t.get("prompt_n"))
            predicted_n = _to_int(t.get("predicted_n"))
            if cache_n or prompt_n or predicted_n:
                return TokenUsage(cache_n + prompt_n, predicted_n, cache_n, prompt_n)
        u = d.get("usage")
        if isinstance(u, dict):
            prompt_tokens = _to_int(u.get("prompt_tokens"))
            completion_tokens = _to_int(u.get("completion_tokens"))
            if prompt_tokens or completion_tokens:
                details = u.get("prompt_tokens_details") or {}
                cached = min(_to_int(details.get("cached_tokens")), prompt_tokens)
                return TokenUsage(
                    prompt_tokens, completion_tokens, cached, max(0, prompt_tokens - cached)
                )
    return TokenUsage(0, 0, 0, 0)


@_safe
def parse_anthropic(body: bytes) -> TokenUsage:
    s = _body_str(body)
    in_base = cache_read = cache_create = out = 0
    if _is_sse(s):
        for payload in iter_blocks(s):
            d = _try_json(payload)
            if not isinstance(d, dict):
                continue
            etype = d.get("type")
            if etype == "message_start":
                u = (d.get("message") or {}).get("usage") or {}
                in_base = _to_int(u.get("input_tokens"))
                cache_read = _to_int(u.get("cache_read_input_tokens"))
                cache_create = _to_int(u.get("cache_creation_input_tokens"))
            elif etype == "message_delta":
                u = d.get("usage") or {}
                if "output_tokens" in u:
                    out = _to_int(u.get("output_tokens"))
    else:
        u = (_try_json(s) or {}).get("usage") or {}
        in_base = _to_int(u.get("input_tokens"))
        cache_read = _to_int(u.get("cache_read_input_tokens"))
        cache_create = _to_int(u.get("cache_creation_input_tokens"))
        out = _to_int(u.get("output_tokens"))
    if not (in_base or cache_read or cache_create or out):
        return TokenUsage(0, 0, 0, 0)
    return TokenUsage(in_base + cache_read + cache_create, out, cache_read, in_base + cache_create)


@_safe
def parse_responses(body: bytes) -> TokenUsage:
    s = _body_str(body)
    usage = {}
    if _is_sse(s):
        for payload in iter_blocks(s):
            d = _try_json(payload)
            if not isinstance(d, dict):
                continue
            if d.get("type") in ("response.completed", "response.incomplete"):
                resp = d.get("response") or {}
                u = resp.get("usage")
                if isinstance(u, dict):
                    usage = u
    else:
        obj = _try_json(s)
        if isinstance(obj, dict):
            usage = obj.get("usage") or {}
    input_tokens = _to_int(usage.get("input_tokens"))
    output_tokens = _to_int(usage.get("output_tokens"))
    if not (input_tokens or output_tokens):
        return TokenUsage(0, 0, 0, 0)
    details = usage.get("input_tokens_details") or {}
    cached = min(_to_int(details.get("cached_tokens")), input_tokens)
    return TokenUsage(input_tokens, output_tokens, cached, max(0, input_tokens - cached))


@_safe
def _parse_generic_usage(body: bytes) -> TokenUsage:
    """裸 usage(input_tokens/output_tokens,无缓存字段)的保守口径:总输入/输出
    明确可取,cache 拆分未知 → cache=0、prompt=input。SSE 取首个带用量的块。"""
    s = _body_str(body)
    inp = out = 0
    if _is_sse(s):
        for payload in iter_blocks(s):
            d = _try_json(payload)
            if not isinstance(d, dict):
                continue
            u = d.get("usage")
            if isinstance(u, dict) and ("input_tokens" in u or "output_tokens" in u):
                inp = _to_int(u.get("input_tokens"))
                out = _to_int(u.get("output_tokens"))
                if inp or out:
                    break
    else:
        obj = _try_json(s)
        if isinstance(obj, dict):
            u = obj.get("usage")
            if isinstance(u, dict):
                inp = _to_int(u.get("input_tokens"))
                out = _to_int(u.get("output_tokens"))
    if not (inp or out):
        return TokenUsage(0, 0, 0, 0)
    return TokenUsage(inp, out, 0, inp)


@_safe
def _parse_ollama(body: bytes) -> TokenUsage:
    """Ollama /api/chat、/api/generate:prompt_eval_count(输入)+ eval_count(输出),
    流式末块(done=true)带计数字段。无缓存拆分 → cache=0、prompt=input。"""
    s = _body_str(body)
    inp = out = 0
    if _is_sse(s):
        for payload in iter_blocks(s):
            d = _try_json(payload)
            if not isinstance(d, dict):
                continue
            inp = _to_int(d.get("prompt_eval_count"))
            out = _to_int(d.get("eval_count"))
            if inp or out:
                break
    else:
        d = _try_json(s) or {}
        inp = _to_int(d.get("prompt_eval_count"))
        out = _to_int(d.get("eval_count"))
    if not (inp or out):
        return TokenUsage(0, 0, 0, 0)
    return TokenUsage(inp, out, 0, inp)


@_safe
def _parse_gemini(body: bytes) -> TokenUsage:
    """Gemini generateContent/streamGenerateContent:usageMetadata。
    promptTokenCount(输入)+ candidatesTokenCount(输出)+ cachedContentTokenCount(缓存读)。
    流式每块都带 usageMetadata,取最末块(累积口径)。"""
    s = _body_str(body)
    inp = out = cached = 0
    if _is_sse(s):
        for payload in iter_blocks(s):
            d = _try_json(payload)
            if not isinstance(d, dict):
                continue
            u = d.get("usageMetadata")
            if isinstance(u, dict) and "promptTokenCount" in u:
                inp = _to_int(u.get("promptTokenCount"))
                out = _to_int(u.get("candidatesTokenCount"))
                cached = _to_int(u.get("cachedContentTokenCount"))
    else:
        u = (_try_json(s) or {}).get("usageMetadata") or {}
        inp = _to_int(u.get("promptTokenCount"))
        out = _to_int(u.get("candidatesTokenCount"))
        cached = _to_int(u.get("cachedContentTokenCount"))
    if not (inp or out):
        return TokenUsage(0, 0, 0, 0)
    cached = min(cached, inp)
    return TokenUsage(inp, out, cached, max(0, inp - cached))


@_safe
def _parse_billed_units(body: bytes) -> TokenUsage:
    """Cohere:meta.billed_units.input_tokens/output_tokens,流式末块带 meta。
    无缓存字段 → cache=0、prompt=input。"""
    s = _body_str(body)
    inp = out = 0
    if _is_sse(s):
        for payload in iter_blocks(s):
            d = _try_json(payload)
            if not isinstance(d, dict):
                continue
            u = ((d.get("meta") or {}).get("billed_units")) or {}
            if "input_tokens" in u or "output_tokens" in u:
                inp = _to_int(u.get("input_tokens"))
                out = _to_int(u.get("output_tokens"))
                if inp or out:
                    break
    else:
        u = ((_try_json(s) or {}).get("meta") or {}).get("billed_units") or {}
        inp = _to_int(u.get("input_tokens"))
        out = _to_int(u.get("output_tokens"))
    if not (inp or out):
        return TokenUsage(0, 0, 0, 0)
    return TokenUsage(inp, out, 0, inp)


@_safe
def parse_generic(body: bytes) -> TokenUsage:
    """保守通用回退:未知路径时按「存在哪些字段」分类(非顺序盲试),避免
    anthropic 与 responses 同字段不同 cache 语义的错配。仅无歧义信号返回非零,
    否则归零——宁可漏计也不误记(用量/计费是 DB 事实)。显式注册表仍为主路。"""
    s = _body_str(body)
    if "input_tokens_details" in s:
        return parse_responses(body)  # OpenAI Responses 缓存口径
    if "cache_read_input_tokens" in s or "cache_creation_input_tokens" in s:
        return parse_anthropic(body)  # Anthropic 缓存口径
    if "prompt_eval_count" in s or "eval_count" in s:
        return _parse_ollama(body)  # Ollama 原生口径
    if "promptTokenCount" in s or "candidatesTokenCount" in s:
        return _parse_gemini(body)  # Gemini usageMetadata 口径
    if "billed_units" in s:
        return _parse_billed_units(body)  # Cohere 计费单位口径
    if (
        "usage" in s
        and "input_tokens" in s
        and not ("prompt_tokens" in s or "completion_tokens" in s)
    ):
        return _parse_generic_usage(body)  # 裸 input/output,无缓存字段
    return parse_openai(body)  # openai 形态(prompt_tokens/completion_tokens)或 llama.cpp timings


# path → (parser, include_usage) 单源;parser_registry 派生保持既有 API 不变。
# 未注册路径 → parse_tokens 回退 parse_generic(见模块 docstring)。
_PARSER_META: dict[str, dict] = {
    "v1/chat/completions": {"parser": parse_openai, "include_usage": True},
    "v1/completions": {"parser": parse_openai, "include_usage": True},
    "v1/embeddings": {"parser": parse_openai, "include_usage": False},
    "v1/rerank": {"parser": parse_openai, "include_usage": False},
    "rerank": {"parser": parse_openai, "include_usage": False},
    "infill": {  # llama.cpp native:timings 头尾带 cache_n/prompt_n/predicted_n
        "parser": parse_openai,
        "include_usage": False,
    },
    "v1/messages": {"parser": parse_anthropic, "include_usage": False},
    "v1/responses": {"parser": parse_responses, "include_usage": False},
}

parser_registry: dict[str, Callable[[bytes], TokenUsage]] = {
    k: v["parser"] for k, v in _PARSER_META.items()
}


def parse_tokens(path: str, body: bytes) -> TokenUsage:
    key = path.lstrip("/").split("?")[0]
    return parser_registry.get(key, parse_generic)(body)


def needs_include_usage(path: str) -> bool:
    key = path.lstrip("/").split("?")[0]
    return _PARSER_META.get(key, {}).get("include_usage", False)
