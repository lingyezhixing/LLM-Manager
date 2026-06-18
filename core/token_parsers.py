"""Path-keyed token parsers.

每个 parser: (body: bytes) -> (input_tokens, output_tokens, cache_n, prompt_n)。
异常安全(@_safe):任何错误返回 (0,0,0,0),绝不抛。
字段映射见 docs/superpowers/specs/2026-06-19-token-parser-registry-design.md §4。
"""
import json
import logging
from typing import Callable, Tuple

logger = logging.getLogger(__name__)


def _safe(parser: Callable) -> Callable:
    """装饰器:保证 parser 绝不抛(流式 finally 里抛会截断客户端流)。"""
    def wrapped(body: bytes) -> Tuple[int, int, int, int]:
        try:
            return parser(body)
        except Exception as e:
            logger.debug(f"[parser] {parser.__name__} 提取失败: {e}")
            return (0, 0, 0, 0)
    wrapped.__name__ = parser.__name__
    return wrapped


def _body_str(body: bytes) -> str:
    return body.decode("utf-8", errors="replace")


def _is_sse(s: str) -> bool:
    """真 SSE 总有 data:/event: 在行首;JSON 串值里的 "data: " 子串不会在行首出现。"""
    for line in s.splitlines():
        ls = line.lstrip()
        if ls.startswith("data:") or ls.startswith("event:"):
            return True
    return False


def _sse_payloads(s: str):
    """正序产出每个 'data: <payload>' 的 payload 字符串,跳过 [DONE]。"""
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
def parse_openai(body: bytes) -> Tuple[int, int, int, int]:
    """/v1/chat/completions + /v1/completions + /v1/embeddings + /v1/rerank + /rerank。
    优先 timings(llama.cpp chat/completions/completions);降级 usage(其余)。
    usage 的 prompt_tokens 是【总量含缓存】→ prompt_n = prompt_tokens - cached_tokens。"""
    s = _body_str(body)
    if _is_sse(s):
        blocks = list(reversed(list(_sse_payloads(s))))   # 末块优先
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
                return (cache_n + prompt_n, predicted_n, cache_n, prompt_n)
        u = d.get("usage")
        if isinstance(u, dict):
            prompt_tokens = _to_int(u.get("prompt_tokens"))
            completion_tokens = _to_int(u.get("completion_tokens"))
            if prompt_tokens or completion_tokens:
                details = u.get("prompt_tokens_details") or {}
                cached = min(_to_int(details.get("cached_tokens")), prompt_tokens)
                cache_n = cached
                prompt_n = max(0, prompt_tokens - cached)
                return (prompt_tokens, completion_tokens, cache_n, prompt_n)
    return (0, 0, 0, 0)


@_safe
def parse_anthropic(body: bytes) -> Tuple[int, int, int, int]:
    """占位,Task 2 实现。"""
    return (0, 0, 0, 0)


@_safe
def parse_responses(body: bytes) -> Tuple[int, int, int, int]:
    """占位,Task 3 实现。"""
    return (0, 0, 0, 0)


def _parse_noop(body: bytes) -> Tuple[int, int, int, int]:
    return (0, 0, 0, 0)


parser_registry: dict[str, Callable] = {
    "v1/chat/completions": parse_openai,
    "v1/completions": parse_openai,
    "v1/embeddings": parse_openai,
    "v1/rerank": parse_openai,
    "rerank": parse_openai,
    "v1/messages": parse_anthropic,
    "v1/responses": parse_responses,
}


def parse_tokens(path: str, body: bytes) -> Tuple[int, int, int, int]:
    """按 path 分派。未知 path -> _parse_noop(由调用方全零守卫跳过)。"""
    key = path.lstrip("/").split("?")[0]
    return parser_registry.get(key, _parse_noop)(body)
