"""Path-keyed token parsers (ported from core/token_parsers.py).

Each parser: (body: bytes) -> TokenUsage. Exception-safe (@_safe): any error
returns TokenUsage(0,0,0,0) — never raises (call sites include streaming finally
blocks). Registered into ports.metering.token_parsers and ports.gateway.endpoint_shapes.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from llm_manager.domain.meter import TokenUsage
from llm_manager.ports.gateway import EndpointShape, endpoint_shapes
from llm_manager.ports.metering import token_parsers

logger = logging.getLogger(__name__)


def _safe(parser: Callable[[bytes], TokenUsage]) -> Callable[[bytes], TokenUsage]:
    def wrapped(body: bytes) -> TokenUsage:
        try:
            return parser(body)
        except Exception as e:  # noqa: BLE001
            logger.debug("[parser] %s failed: %s", parser.__name__, e)
            return TokenUsage(0, 0, 0, 0)

    wrapped.__name__ = parser.__name__
    return wrapped


def _body_str(body: bytes) -> str:
    return body.decode("utf-8", errors="replace")


def _is_sse(s: str) -> bool:
    for line in s.splitlines():
        ls = line.lstrip()
        if ls.startswith("data:") or ls.startswith("event:"):
            return True
    return False


def _sse_payloads(s: str):
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
    """/v1/chat/completions + /v1/completions + /v1/embeddings + /v1/rerank.
    Prefer timings (llama.cpp); fall back to usage. cached_tokens subtracted from prompt."""
    s = _body_str(body)
    if _is_sse(s):
        blocks = list(reversed(list(_sse_payloads(s))))  # last chunk first
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
                prompt_n = max(0, prompt_tokens - cached)
                return TokenUsage(prompt_tokens, completion_tokens, cached, prompt_n)
    return TokenUsage(0, 0, 0, 0)


@_safe
def parse_anthropic(body: bytes) -> TokenUsage:
    """/v1/messages. input_tokens is the non-cached base; cache_read/create additive."""
    s = _body_str(body)
    in_base = cache_read = cache_create = out = 0
    if _is_sse(s):
        for payload in _sse_payloads(s):
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
    """/v1/responses. Terminal response.completed/incomplete carries response.usage."""
    s = _body_str(body)
    usage: dict = {}
    if _is_sse(s):
        for payload in _sse_payloads(s):
            d = _try_json(payload)
            terminal = d.get("type") in ("response.completed", "response.incomplete")
            if isinstance(d, dict) and terminal:
                u = (d.get("response") or {}).get("usage")
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


def _noop(body: bytes) -> TokenUsage:
    return TokenUsage(0, 0, 0, 0)


# --- Registration (the @token_parser seam; idempotent so module re-exec is safe) ---
for _path in ("v1/chat/completions", "v1/completions", "v1/embeddings", "v1/rerank", "rerank"):
    if _path not in token_parsers:
        token_parsers.register(_path)(parse_openai)
if "v1/messages" not in token_parsers:
    token_parsers.register("v1/messages")(parse_anthropic)
if "v1/responses" not in token_parsers:
    token_parsers.register("v1/responses")(parse_responses)

# Endpoints whose streaming responses need stream_options.include_usage=True so
# lmdeploy/llama.cpp emit usage in the final SSE chunk (spec §16).
for _path in ("v1/chat/completions", "v1/completions"):
    if _path not in endpoint_shapes:
        endpoint_shapes.register(_path)(EndpointShape(needs_include_usage=True))


def parse_tokens(path: str, body: bytes) -> TokenUsage:
    """Dispatch by normalized path; unknown path -> zero (no raise)."""
    key = path.lstrip("/").split("?")[0]
    parser = token_parsers.get(key, default=_noop)
    return parser(body)
