# Phase B: token parser_registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `TokenTracker` correctly extract tokens for all chat-family APIs (chat/completions, completions, messages, responses) plus embeddings and rerank, streaming + non-streaming, on BOTH llama.cpp and lmdeploy — via a path-keyed `parser_registry`, with streaming-usage injection so lmdeploy streams are trackable.

**Architecture:** New pure-parsing module `core/token_parsers.py` (`parse_openai` / `parse_anthropic` / `parse_responses` + registry + `parse_tokens` dispatcher + `@_safe` exception wrapper). `TokenTracker.extract_tokens_from_response(content, path)` dispatches by path. `route_request` threads `path` into both branches AND injects `stream_options.include_usage` for streaming chat/completions/completions (lmdeploy needs it). No DB/webui/billing changes.

**Tech Stack:** Python 3, FastAPI, httpx, pytest 8.3.5, Git Bash on Windows.

**Reference spec:** `docs/superpowers/specs/2026-06-19-token-parser-registry-design.md` (all field mappings + cross-backend matrix verified by live capture on llama.cpp + lmdeploy, 2026-06-19).

**Branch:** `refactor/probe-registry-phase-a` (continues; Phase A complete, Phase B-1 spec approved).

**Test approach:** parser tests use SMALL INLINE fixtures (real captured usage/timings excerpts, vectors/text stripped) — no fixture-file management, deterministic, no .gitignore dependency.

**Verified target values (warm cache, max_tokens=8 for chat) used in assertions:**
- llama.cpp chat: timings cache_n=15/prompt_n=4/predicted_n=8 → (input=19, out=8, cache_n=15, prompt_n=4)
- lmdeploy chat: usage prompt_tokens=17/completion_tokens=13 (no cache) → (19? no: input=17, out=13, cache_n=0, prompt_n=17)
- llama.cpp messages: input_tokens=4/cache_read=15/output=8 → (19, 8, 15, 4)
- llama.cpp responses: input_tokens=19/cached=15/output=8 → (19, 8, 15, 4)
- lmdeploy responses (incomplete): input_tokens=10/cached=0/output=10 → (10, 10, 0, 10)
- llama.cpp embedding: prompt_tokens=5 → (5, 0, 0, 5)
- lmdeploy embedding: prompt_tokens=3/completion_tokens=0 → (3, 0, 0, 3)
- llama.cpp rerank: prompt_tokens=47 → (47, 0, 0, 47)

---

## Task 1: `core/token_parsers.py` skeleton + helpers + `parse_openai` + dispatcher

**Files:**
- Create: `core/token_parsers.py`
- Create: `tests/test_token_parsers_openai.py`

Create the new pure-parsing module with shared helpers, the `@_safe` decorator, the `parse_tokens` dispatcher + `parser_registry` (parse_openai wired for openai-family paths; anthropic/responses entries point to stubs filled in Tasks 2-3), and `parse_openai` (timings branch + usage branch). Test `parse_openai` + dispatcher routing + exception safety.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_token_parsers_openai.py`:
```python
import json
from core.token_parsers import parse_tokens, parse_openai


def _sse(*payloads):
    """Build an SSE byte string from data payloads."""
    return ("\n\n".join(f"data: {p}" for p in payloads) + "\n\ndata: [DONE]\n").encode()


# ---- parse_openai: timings branch (llama.cpp chat/completions) ----

def test_openai_nostream_timings_llamacpp():
    # llama.cpp chat/completions non-stream: timings + usage top-level
    body = json.dumps({
        "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 19, "completion_tokens": 8,
                  "prompt_tokens_details": {"cached_tokens": 15}},
        "timings": {"cache_n": 15, "prompt_n": 4, "predicted_n": 8},
    }).encode()
    assert parse_openai(body) == (19, 8, 15, 4)


def test_openai_stream_timings_only_llamacpp():
    # llama.cpp chat stream: LAST chunk carries timings, NO usage anywhere
    mid = json.dumps({"choices": [{"delta": {"content": "x"}}], "object": "chat.completion.chunk"})
    last = json.dumps({"choices": [{"delta": {}, "finish_reason": "length"}],
                       "object": "chat.completion.chunk",
                       "timings": {"cache_n": 15, "prompt_n": 4, "predicted_n": 8}})
    assert parse_openai(_sse(mid, last)) == (19, 8, 15, 4)


# ---- parse_openai: usage branch (lmdeploy + embeddings + rerank) ----

def test_openai_nostream_usage_lmdeploy_no_timings():
    body = json.dumps({
        "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 17, "total_tokens": 30, "completion_tokens": 13},
    }).encode()
    # no timings -> usage branch; no cached field -> cache_n=0, prompt_n=17
    assert parse_openai(body) == (17, 13, 0, 17)


def test_openai_stream_usage_lmdeploy_with_include_usage():
    # lmdeploy stream w/ include_usage: final choices:[] chunk + usage
    mid = json.dumps({"choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}],
                      "usage": None})
    last = json.dumps({"choices": [], "usage": {"prompt_tokens": 17, "completion_tokens": 13}})
    assert parse_openai(_sse(mid, last)) == (17, 13, 0, 17)


def test_openai_embeddings_llamacpp():
    # llama.cpp embedding: usage has only prompt_tokens+total_tokens (no completion, no details)
    body = json.dumps({"object": "list", "data": [{"embedding": [0.1], "index": 0}],
                       "usage": {"prompt_tokens": 5, "total_tokens": 5}}).encode()
    assert parse_openai(body) == (5, 0, 0, 5)


def test_openai_rerank_llamacpp():
    body = json.dumps({"object": "list", "results": [{"index": 0, "relevance_score": 6.1}],
                       "usage": {"prompt_tokens": 47, "total_tokens": 47}}).encode()
    assert parse_openai(body) == (47, 0, 0, 47)


# ---- dispatcher routing ----

def test_parse_tokens_routes_chat_to_openai():
    body = json.dumps({"usage": {"prompt_tokens": 5, "completion_tokens": 0}}).encode()
    assert parse_tokens("v1/chat/completions", body) == (5, 0, 0, 5)


def test_parse_tokens_unknown_path_is_noop():
    body = json.dumps({"usage": {"prompt_tokens": 5}}).encode()
    assert parse_tokens("v1/whatever", body) == (0, 0, 0, 0)


def test_parse_tokens_normalizes_path():
    body = json.dumps({"usage": {"prompt_tokens": 5, "completion_tokens": 0}}).encode()
    assert parse_tokens("/v1/chat/completions?beta=true", body) == (5, 0, 0, 5)


# ---- exception safety ----

def test_openai_corrupted_body_returns_zero():
    assert parse_openai(b"not json at all {{{") == (0, 0, 0, 0)
    assert parse_openai(b"") == (0, 0, 0, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_token_parsers_openai.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.token_parsers'`

- [ ] **Step 3: Create `core/token_parsers.py`**

Create `core/token_parsers.py`:
```python
"""Path-keyed token parsers.

每个 parser: (body: bytes) -> (input_tokens, output_tokens, cache_n, prompt_n)。
异常安全( @_safe ):任何错误返回 (0,0,0,0),绝不抛。
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
    return "data: " in s or s.lstrip().startswith("event:")


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
        # 1) timings(llama.cpp)
        t = d.get("timings")
        if isinstance(t, dict):
            cache_n = _to_int(t.get("cache_n"))
            prompt_n = _to_int(t.get("prompt_n"))
            predicted_n = _to_int(t.get("predicted_n"))
            if cache_n or prompt_n or predicted_n:
                return (cache_n + prompt_n, predicted_n, cache_n, prompt_n)
        # 2) usage(OpenAI 系,总量含缓存)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_token_parsers_openai.py -v`
Expected: `10 passed`

- [ ] **Step 5: Commit**
```bash
git add core/token_parsers.py tests/test_token_parsers_openai.py
git commit -m "feat: 新增 core/token_parsers.py —— parse_openai + dispatcher + 异常安全"
```

---

## Task 2: `parse_anthropic` (/v1/messages, merge message_start + message_delta)

**Files:**
- Modify: `core/token_parsers.py` (implement `parse_anthropic`)
- Create: `tests/test_token_parsers_anthropic.py`

`input_tokens` is the NON-CACHED base. SSE: merge `message_start` usage (input/cache_read/cache_creation) with the LAST `message_delta` usage (output_tokens). `.get(.., 0)` for cache_creation (absent on llama.cpp).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_token_parsers_anthropic.py`:
```python
import json
from core.token_parsers import parse_anthropic, parse_tokens


def _sse(events):
    """events: list of (event_name, data_dict)."""
    out = []
    for name, data in events:
        out.append(f"event: {name}\ndata: {json.dumps(data)}")
    return ("\n\n".join(out) + "\n").encode()


def test_anthropic_nostream():
    body = json.dumps({
        "type": "message",
        "content": [{"type": "text", "text": "x"}],
        "usage": {"cache_read_input_tokens": 15, "input_tokens": 4, "output_tokens": 8},
    }).encode()
    # input_tokens=4 non-cached base; cache_n=15; prompt_n=4; input_total=19
    assert parse_anthropic(body) == (19, 8, 15, 4)


def test_anthropic_stream_merge_start_and_delta():
    events = [
        ("message_start", {"type": "message_start",
            "message": {"usage": {"cache_read_input_tokens": 15, "input_tokens": 4, "output_tokens": 0}}}),
        ("content_block_delta", {"type": "content_block_delta", "delta": {"text": "x"}}),
        ("message_delta", {"type": "message_delta",
            "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 8}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    assert parse_anthropic(_sse(events)) == (19, 8, 15, 4)


def test_anthropic_takes_last_message_delta_output():
    # if multiple message_delta, last one's output wins (cumulative)
    events = [
        ("message_start", {"type": "message_start",
            "message": {"usage": {"input_tokens": 4, "cache_read_input_tokens": 0, "output_tokens": 0}}}),
        ("message_delta", {"type": "message_delta", "usage": {"output_tokens": 3}}),
        ("message_delta", {"type": "message_delta", "usage": {"output_tokens": 8}}),
    ]
    assert parse_anthropic(_sse(events)) == (4, 8, 0, 4)


def test_anthropic_cache_creation_added_to_prompt_n():
    # if cache_creation_input_tokens present, it's a non-cached write -> prompt_n
    body = json.dumps({"usage": {"input_tokens": 4, "cache_creation_input_tokens": 10,
                                 "cache_read_input_tokens": 15, "output_tokens": 8}}).encode()
    # cache_n=15, prompt_n=4+10=14, input_total=4+10+15=29
    assert parse_anthropic(body) == (29, 8, 15, 14)


def test_anthropic_via_dispatcher():
    body = json.dumps({"usage": {"input_tokens": 4, "cache_read_input_tokens": 15, "output_tokens": 8}}).encode()
    assert parse_tokens("v1/messages", body) == (19, 8, 15, 4)


def test_anthropic_corrupted_returns_zero():
    assert parse_anthropic(b"garbage{") == (0, 0, 0, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_token_parsers_anthropic.py -v`
Expected: stream-merge tests FAIL (stub returns 0,0,0,0)

- [ ] **Step 3: Implement `parse_anthropic`**

In `core/token_parsers.py`, replace the `parse_anthropic` stub with:
```python
@_safe
def parse_anthropic(body: bytes) -> Tuple[int, int, int, int]:
    """/v1/messages(仅 llama.cpp;lmdeploy 不支持)。无 timings。
    input_tokens 是【非缓存基准】→ cache_n=cache_read, prompt_n=input+cache_creation。
    流式:正序合并 message_start(input/cache 类)+ 末个 message_delta(output_tokens)。"""
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
                    out = _to_int(u.get("output_tokens"))   # 末个 message_delta 的累计 output
    else:
        u = (_try_json(s) or {}).get("usage") or {}
        in_base = _to_int(u.get("input_tokens"))
        cache_read = _to_int(u.get("cache_read_input_tokens"))
        cache_create = _to_int(u.get("cache_creation_input_tokens"))
        out = _to_int(u.get("output_tokens"))

    if not (in_base or cache_read or cache_create or out):
        return (0, 0, 0, 0)
    cache_n = cache_read
    prompt_n = in_base + cache_create
    return (in_base + cache_read + cache_create, out, cache_n, prompt_n)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_token_parsers_anthropic.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit**
```bash
git add core/token_parsers.py tests/test_token_parsers_anthropic.py
git commit -m "feat: parse_anthropic —— /v1/messages 流式合并 message_start+message_delta"
```

---

## Task 3: `parse_responses` (/v1/responses, nested response.usage, completed/incomplete)

**Files:**
- Modify: `core/token_parsers.py` (implement `parse_responses`)
- Create: `tests/test_token_parsers_responses.py`

`input_tokens` is TOTAL incl cache. Stream: find the terminal `response.completed` OR `response.incomplete` event, read NESTED `data["response"]["usage"]` (NOT top-level). Non-stream: top-level `usage`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_token_parsers_responses.py`:
```python
import json
from core.token_parsers import parse_responses, parse_tokens


def _sse(events):
    out = []
    for name, data in events:
        out.append(f"event: {name}\ndata: {json.dumps(data)}")
    return ("\n\n".join(out) + "\n").encode()


def test_responses_nostream():
    body = json.dumps({
        "object": "response", "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "x"}]}],
        "usage": {"input_tokens": 19, "output_tokens": 8, "total_tokens": 27,
                  "input_tokens_details": {"cached_tokens": 15}},
    }).encode()
    assert parse_responses(body) == (19, 8, 15, 4)


def test_responses_stream_completed():
    events = [
        ("response.created", {"type": "response.created",
            "response": {"id": "r1", "status": "in_progress"}}),
        ("response.output_text.delta", {"type": "response.output_text.delta", "delta": "x"}),
        ("response.completed", {"type": "response.completed",
            "response": {"id": "r1", "status": "completed",
                "usage": {"input_tokens": 19, "output_tokens": 8,
                          "input_tokens_details": {"cached_tokens": 15}}}}),
    ]
    assert parse_responses(_sse(events)) == (19, 8, 15, 4)


def test_responses_stream_incomplete_lmdeploy():
    # lmdeploy emits response.incomplete (truncation) — must also be matched
    events = [
        ("response.created", {"type": "response.created", "response": {"status": "in_progress"}}),
        ("response.incomplete", {"type": "response.incomplete",
            "response": {"status": "incomplete",
                "usage": {"input_tokens": 10, "output_tokens": 10,
                          "input_tokens_details": {"cached_tokens": 0}}}}),
    ]
    assert parse_responses(_sse(events)) == (10, 10, 0, 10)


def test_responses_via_dispatcher():
    body = json.dumps({"usage": {"input_tokens": 19, "output_tokens": 8,
                                 "input_tokens_details": {"cached_tokens": 15}}}).encode()
    assert parse_tokens("v1/responses", body) == (19, 8, 15, 4)


def test_responses_corrupted_returns_zero():
    assert parse_responses(b"") == (0, 0, 0, 0)
    assert parse_responses(b"not json") == (0, 0, 0, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_token_parsers_responses.py -v`
Expected: stream + nested tests FAIL (stub returns 0,0,0,0)

- [ ] **Step 3: Implement `parse_responses`**

In `core/token_parsers.py`, replace the `parse_responses` stub with:
```python
@_safe
def parse_responses(body: bytes) -> Tuple[int, int, int, int]:
    """/v1/responses(llama.cpp + lmdeploy)。无 timings。
    input_tokens 是【总量含缓存】→ prompt_n = input_tokens - cached_tokens。
    非流式:顶层 usage。流式:找带 response.usage 的终止事件(completed 或 incomplete),下钻 data['response']['usage']。"""
    s = _body_str(body)
    usage = {}
    if _is_sse(s):
        for payload in _sse_payloads(s):
            d = _try_json(payload)
            if not isinstance(d, dict):
                continue
            if d.get("type") in ("response.completed", "response.incomplete"):
                resp = d.get("response") or {}
                u = resp.get("usage")
                if isinstance(u, dict):
                    usage = u   # 取末个终止事件的 usage
    else:
        obj = _try_json(s)
        if isinstance(obj, dict):
            usage = obj.get("usage") or {}

    input_tokens = _to_int(usage.get("input_tokens"))
    output_tokens = _to_int(usage.get("output_tokens"))
    if not (input_tokens or output_tokens):
        return (0, 0, 0, 0)
    details = usage.get("input_tokens_details") or {}
    cached = min(_to_int(details.get("cached_tokens")), input_tokens)
    cache_n = cached
    prompt_n = max(0, input_tokens - cached)
    return (input_tokens, output_tokens, cache_n, prompt_n)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_token_parsers_responses.py -v`
Expected: `5 passed`

- [ ] **Step 5: Full parser suite + commit**
```bash
python -m pytest tests/test_token_parsers_openai.py tests/test_token_parsers_anthropic.py tests/test_token_parsers_responses.py -v
```
Expected: `21 passed`
```bash
git add core/token_parsers.py tests/test_token_parsers_responses.py
git commit -m "feat: parse_responses —— /v1/responses 嵌套 response.usage,认 completed/incomplete"
```

---

## Task 4: Wire `TokenTracker` to dispatch by path

**Files:**
- Modify: `core/api_router.py`:
  - `extract_tokens_from_response(self, response_content, path)` (line 51) — dispatch via `parse_tokens`
  - `create_stream_with_token_logging(self, model_name, response, request_start_time, path)` (line 152) — add `path` param, pass to `extract_tokens_from_response` on close (line 167)
  - Remove the now-superseded `_extract_tokens` (lines 23-49) — `parse_openai` fully replaces it
- Create: `tests/test_token_tracker_dispatch.py`

`extract_tokens_from_response` becomes a thin dispatcher; `_extract_tokens` (the old path-agnostic extractor) is removed (dead after dispatch). The debug "末尾数据块" logging can stay or simplify — keep a one-line debug log.

- [ ] **Step 1: Write the failing test**

Create `tests/test_token_tracker_dispatch.py`:
```python
import json
from core.api_router import TokenTracker


def _make_tracker():
    # TokenTracker(monitor, config_manager); both only used for recording, not extraction.
    # Pass minimal doubles — extraction does not touch them.
    return TokenTracker(monitor=None, config_manager=None)


def test_extract_dispatches_openai_for_chat():
    body = json.dumps({"timings": {"cache_n": 15, "prompt_n": 4, "predicted_n": 8}}).encode()
    assert _make_tracker().extract_tokens_from_response(body, "v1/chat/completions") == (19, 8, 15, 4)


def test_extract_dispatches_anthropic_for_messages():
    body = json.dumps({"usage": {"input_tokens": 4, "cache_read_input_tokens": 15, "output_tokens": 8}}).encode()
    assert _make_tracker().extract_tokens_from_response(body, "v1/messages") == (19, 8, 15, 4)


def test_extract_dispatches_responses():
    body = json.dumps({"usage": {"input_tokens": 19, "output_tokens": 8,
                                 "input_tokens_details": {"cached_tokens": 15}}}).encode()
    assert _make_tracker().extract_tokens_from_response(body, "v1/responses") == (19, 8, 15, 4)


def test_extract_unknown_path_returns_zero():
    assert _make_tracker().extract_tokens_from_response(b'{"usage": {"prompt_tokens": 5}}', "v1/whatever") == (0, 0, 0, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_token_tracker_dispatch.py -v`
Expected: FAIL — `extract_tokens_from_response()` currently takes 1 arg (no `path`); `TypeError` or wrong result.

- [ ] **Step 3: Rewrite `extract_tokens_from_response` + `create_stream_with_token_logging`; remove `_extract_tokens`**

(a) Replace `extract_tokens_from_response` (currently lines ~51-125) with:
```python
    def extract_tokens_from_response(self, response_content: bytes, path: str) -> tuple[int, int, int, int]:
        """从响应中按 path 分派到对应解析器提取 Token。"""
        from core.token_parsers import parse_tokens
        return parse_tokens(path, response_content)
```
(Delete the old reverse-scan body and the `_extract_tokens` method entirely — lines ~23-49 and the old `extract_tokens_from_response` internals. `parse_openai` supersedes `_extract_tokens`.)

(b) Change the signature of `create_stream_with_token_logging` (line ~152) to add `path`:
```python
    async def create_stream_with_token_logging(self, model_name: str, response: any, request_start_time: float, path: str):
```
and the call inside its `finally` (line ~167) becomes:
```python
                input_tokens, output_tokens, cache_n, prompt_n = self.extract_tokens_from_response(full_content, path)
```

- [ ] **Step 4: Run test to verify it passes + import smoke**
```bash
python -m pytest tests/test_token_tracker_dispatch.py -v
python -c "from core.api_router import APIRouter, TokenTracker; print('ok')"
```
Expected: `4 passed`; prints `ok`.

- [ ] **Step 5: Commit**
```bash
git add core/api_router.py tests/test_token_tracker_dispatch.py
git commit -m "refactor: TokenTracker 按 path 分派解析,移除旧 _extract_tokens"
```

---

## Task 5: `route_request` — thread path + inject streaming `include_usage`

**Files:**
- Modify: `core/api_router.py` `route_request`:
  - Inject `stream_options.include_usage` for streaming chat/completions (after line 248, before 249)
  - Pass `path` to `create_stream_with_token_logging` (line 351-353)
  - Pass `path` to `extract_tokens_from_response` (line 376)
- Create: `tests/test_route_include_usage.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_route_include_usage.py`:
```python
def test_route_request_injects_include_usage_for_streaming_chat():
    """Source-level guard: route_request must inject stream_options.include_usage
    for streaming v1/chat/completions + v1/completions (lmdeploy needs it)."""
    import os
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(repo, "core", "api_router.py"), encoding="utf-8").read()
    start = src.index("async def route_request")
    body = src[start:]
    assert "include_usage" in body, "route_request must inject stream_options.include_usage"
    assert "setdefault" in body or "include_usage" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_route_include_usage.py -v`
Expected: FAIL (no `include_usage` in route_request yet)

- [ ] **Step 3: Apply the three edits to `route_request`**

(a) Inject `include_usage`. Replace the block (lines 247-249):
```python
                body = await request.json()
                model_alias = body.get("model")
                request_data = json.dumps(body).encode('utf-8')
```
with:
```python
                body = await request.json()
                model_alias = body.get("model")
                # 流式 chat/completions 注入 include_usage,确保 lmdeploy 等后端末块返回 usage 供 token 追踪
                if body.get("stream") is True:
                    _norm_path = path.lstrip("/").split("?")[0]
                    if _norm_path in ("v1/chat/completions", "v1/completions"):
                        _so = body.get("stream_options") or {}
                        _so.setdefault("include_usage", True)
                        body["stream_options"] = _so
                request_data = json.dumps(body).encode('utf-8')
```

(b) Thread `path` into the streaming wrapper (lines 351-353). Replace:
```python
                    token_logging_stream = token_tracker.create_stream_with_token_logging(
                        model_name, response, request_start_time
                    )
```
with:
```python
                    token_logging_stream = token_tracker.create_stream_with_token_logging(
                        model_name, response, request_start_time, path
                    )
```

(c) Thread `path` into the non-streaming extract (line 376). Replace:
```python
                    input_tokens, output_tokens, cache_n, prompt_n = token_tracker.extract_tokens_from_response(content)
```
with:
```python
                    input_tokens, output_tokens, cache_n, prompt_n = token_tracker.extract_tokens_from_response(content, path)
```

- [ ] **Step 4: Run test + full suite + import smoke**
```bash
python -m pytest tests/ -v
python -c "from core.api_router import APIRouter; print('ok')"
```
Expected: all tests pass (full suite); prints `ok`.

- [ ] **Step 5: Commit**
```bash
git add core/api_router.py tests/test_route_include_usage.py
git commit -m "feat: route_request 流式注入 include_usage 并按 path 分派 token 解析"
```

---

## Task 6: Final code review + live verification (both backends)

**Files:** none (review + manual verification)

- [ ] **Step 1: Dispatch final code reviewer** — review the whole Phase B diff (`git diff b4c6fbe <head>`) against the spec: every parser's 4-tuple mapping, exception safety, the include_usage injection scope, and no regression in chat/completions. (Controller dispatches `superpowers:code-reviewer` with BASE=`b4c6fbe`, HEAD=HEAD.)

- [ ] **Step 2: Live verification — llama.cpp chat/embedding/rerank.** With a llama.cpp backend configured, via the manager (default 8080):
```bash
# chat/completions (should record non-zero; timings path)
curl -s http://127.0.0.1:8080/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"<alias>","messages":[{"role":"user","content":"ping"}],"max_tokens":8,"stream":false}'
# /v1/messages (should NOW record non-zero — the Phase B fix)
curl -s http://127.0.0.1:8080/v1/messages -H "Content-Type: application/json" -d '{"model":"<alias>","max_tokens":8,"messages":[{"role":"user","content":"ping"}]}'
python -c "import sqlite3;print(sqlite3.connect('webui/monitoring.db').execute('SELECT input_tokens,output_tokens,cache_n,prompt_n FROM model_requests ORDER BY id DESC LIMIT 3').fetchall())"
```
Expected: chat row + messages row both non-zero.

- [ ] **Step 3: Live verification — lmdeploy chat (include_usage injection).** With an lmdeploy backend configured, send a STREAMING chat request through the manager and confirm a row is recorded (proves include_usage injection works — without it lmdeploy stream would be 0):
```bash
curl -s -N http://127.0.0.1:8080/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"<lmdeploy-alias>","messages":[{"role":"user","content":"ping"}],"max_tokens":8,"stream":true}'
python -c "import sqlite3;print(sqlite3.connect('webui/monitoring.db').execute('SELECT input_tokens,output_tokens,cache_n,prompt_n FROM model_requests ORDER BY id DESC LIMIT 1').fetchall())"
```
Expected: non-zero row (lmdeploy stream tracked via injected include_usage).

- [ ] **Step 4: Document + branch finish.** If all pass, Phase B verified. Invoke `superpowers:finishing-a-development-branch`.

---

## Self-Review (plan author)

**Spec coverage:** §3.1 token_parsers.py → Tasks 1-3 ✓; §3.2 TokenTracker wiring → Task 4 ✓; §3.3 route_request path threading → Task 5 ✓; §3.4 include_usage injection → Task 5 ✓; §4 4-tuple mappings → encoded in each parser + asserted in tests ✓; §5 scan strategies (reverse for openai, forward-merge for anthropic, terminal-event drill for responses) → Tasks 1-3 ✓; §6 exception safety → `@_safe` + corrupted-fixture tests ✓; §7 tests → inline fixtures per parser + dispatcher + exception + include_usage ✓.

**Placeholder scan:** none — every step has complete code or exact before/after.

**Type/name consistency:** `parse_tokens(path, body)` consistent across tasks; `(input, output, cache_n, prompt_n)` 4-tuple order consistent; `extract_tokens_from_response(content, path)` + `create_stream_with_token_logging(model_name, response, request_start_time, path)` signatures consistent between Task 4 (definition) and Task 5 (call sites).

**Scope note:** embeddings + rerank covered by `parse_openai` usage branch (Task 1, tested). lmdeploy `/v1/responses` `response.incomplete` covered (Task 3, tested). lmdeploy `/v1/messages` unsupported — not testable live, parse_anthropic still correct where `/v1/messages` exists. `probe_reranker` bare-path issue deferred (spec §8).
