"""Path-keyed token parsers. Plain-dict registry + @_safe total exception safety
(a raise in stream-finally truncates the client stream). Ported from legacy
core/token_parsers.py (behavior preserved verbatim)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable

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
        except Exception as e:
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
        if ls.startswith("data:") or ls.startswith("event:"):
            return True
    return False


def iter_blocks(s: str):
    """Yield each 'data: <payload>' payload string (skip [DONE])."""
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
                return TokenUsage(prompt_tokens, completion_tokens, cached, max(0, prompt_tokens - cached))
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


def _parse_noop(body: bytes) -> TokenUsage:
    return TokenUsage(0, 0, 0, 0)


parser_registry: dict[str, Callable[[bytes], TokenUsage]] = {
    "v1/chat/completions": parse_openai,
    "v1/completions": parse_openai,
    "v1/embeddings": parse_openai,
    "v1/rerank": parse_openai,
    "rerank": parse_openai,
    "v1/messages": parse_anthropic,
    "v1/responses": parse_responses,
}

_PATH_META: dict[str, dict] = {
    "v1/chat/completions": {"needs_include_usage": True},
    "v1/completions": {"needs_include_usage": True},
    "v1/embeddings": {"needs_include_usage": False},
    "v1/rerank": {"needs_include_usage": False},
    "rerank": {"needs_include_usage": False},
    "v1/messages": {"needs_include_usage": False},
    "v1/responses": {"needs_include_usage": False},
}


def parse_tokens(path: str, body: bytes) -> TokenUsage:
    key = path.lstrip("/").split("?")[0]
    return parser_registry.get(key, _parse_noop)(body)


def needs_include_usage(path: str) -> bool:
    key = path.lstrip("/").split("?")[0]
    return _PATH_META.get(key, {}).get("needs_include_usage", False)
