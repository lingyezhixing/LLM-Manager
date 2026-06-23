import json
from core.token_parsers import parse_tokens, parse_openai


def _sse(*payloads):
    """Build an SSE byte string from data payloads."""
    return ("\n\n".join(f"data: {p}" for p in payloads) + "\n\ndata: [DONE]\n").encode()


def test_openai_nostream_timings_llamacpp():
    body = json.dumps({
        "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 19, "completion_tokens": 8,
                  "prompt_tokens_details": {"cached_tokens": 15}},
        "timings": {"cache_n": 15, "prompt_n": 4, "predicted_n": 8},
    }).encode()
    assert parse_openai(body) == (19, 8, 15, 4)


def test_openai_stream_timings_only_llamacpp():
    mid = json.dumps({"choices": [{"delta": {"content": "x"}}], "object": "chat.completion.chunk"})
    last = json.dumps({"choices": [{"delta": {}, "finish_reason": "length"}],
                       "object": "chat.completion.chunk",
                       "timings": {"cache_n": 15, "prompt_n": 4, "predicted_n": 8}})
    assert parse_openai(_sse(mid, last)) == (19, 8, 15, 4)


def test_openai_nostream_usage_lmdeploy_no_timings():
    body = json.dumps({
        "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 17, "total_tokens": 30, "completion_tokens": 13},
    }).encode()
    assert parse_openai(body) == (17, 13, 0, 17)


def test_openai_stream_usage_lmdeploy_with_include_usage():
    mid = json.dumps({"choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}],
                      "usage": None})
    last = json.dumps({"choices": [], "usage": {"prompt_tokens": 17, "completion_tokens": 13}})
    assert parse_openai(_sse(mid, last)) == (17, 13, 0, 17)


def test_openai_embeddings_llamacpp():
    body = json.dumps({"object": "list", "data": [{"embedding": [0.1], "index": 0}],
                       "usage": {"prompt_tokens": 5, "total_tokens": 5}}).encode()
    assert parse_openai(body) == (5, 0, 0, 5)


def test_openai_rerank_llamacpp():
    body = json.dumps({"object": "list", "results": [{"index": 0, "relevance_score": 6.1}],
                       "usage": {"prompt_tokens": 47, "total_tokens": 47}}).encode()
    assert parse_openai(body) == (47, 0, 0, 47)


def test_parse_tokens_routes_chat_to_openai():
    body = json.dumps({"usage": {"prompt_tokens": 5, "completion_tokens": 0}}).encode()
    assert parse_tokens("v1/chat/completions", body) == (5, 0, 0, 5)


def test_parse_tokens_unknown_path_is_noop():
    body = json.dumps({"usage": {"prompt_tokens": 5}}).encode()
    assert parse_tokens("v1/whatever", body) == (0, 0, 0, 0)


def test_parse_tokens_normalizes_path():
    body = json.dumps({"usage": {"prompt_tokens": 5, "completion_tokens": 0}}).encode()
    assert parse_tokens("/v1/chat/completions?beta=true", body) == (5, 0, 0, 5)


def test_openai_corrupted_body_returns_zero():
    assert parse_openai(b"not json at all {{{") == (0, 0, 0, 0)
    assert parse_openai(b"") == (0, 0, 0, 0)


def test_openai_json_with_data_substring_not_misdetected_as_sse():
    body = json.dumps({
        "choices": [{"message": {"content": "see the data: must be processed"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 5},
    }).encode()
    assert parse_openai(body) == (12, 5, 0, 12)
