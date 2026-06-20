import json

from llm_manager.domain.meter import TokenUsage
from llm_manager.ports.gateway import endpoint_shapes
from llm_manager.ports.metering import token_parsers


def _sse(*chunks: str) -> bytes:
    return "\n".join(f"data: {c}" for c in chunks).encode()


def test_openai_non_stream_usage():
    body = json.dumps({"usage": {"prompt_tokens": 10, "completion_tokens": 4}}).encode()
    assert token_parsers.get("v1/chat/completions")(body) == TokenUsage(10, 4, 0, 10)


def test_openai_stream_timings_llamacpp():
    last = {"timings": {"cache_n": 3, "prompt_n": 7, "predicted_n": 4}}
    body = _sse(json.dumps({"foo": 1}), json.dumps(last))
    assert token_parsers.get("v1/chat/completions")(body) == TokenUsage(10, 4, 3, 7)


def test_openai_usage_cached_tokens_subtracted():
    body = json.dumps({
        "usage": {
            "prompt_tokens": 10, "completion_tokens": 4,
            "prompt_tokens_details": {"cached_tokens": 6},
        }
    }).encode()
    assert token_parsers.get("v1/chat/completions")(body) == TokenUsage(10, 4, 6, 4)


def test_anthropic_stream_merge():
    start = {
        "type": "message_start",
        "message": {"usage": {
            "input_tokens": 5,
            "cache_read_input_tokens": 2,
            "cache_creation_input_tokens": 1,
        }},
    }
    delta = {"type": "message_delta", "usage": {"output_tokens": 8}}
    body = _sse(json.dumps(start), json.dumps(delta))
    assert token_parsers.get("v1/messages")(body) == TokenUsage(8, 8, 2, 6)


def test_responses_terminal_event_usage():
    term = {
        "type": "response.completed",
        "response": {"usage": {
            "input_tokens": 9,
            "output_tokens": 3,
            "input_tokens_details": {"cached_tokens": 4},
        }},
    }
    body = _sse(json.dumps({"type": "response.output_text.delta"}), json.dumps(term))
    assert token_parsers.get("v1/responses")(body) == TokenUsage(9, 3, 4, 5)


def test_malformed_body_is_safe_and_zero():
    assert token_parsers.get("v1/chat/completions")(b"not json") == TokenUsage(0, 0, 0, 0)


def test_unknown_path_noop():
    from llm_manager.metering.parsers import parse_tokens
    assert parse_tokens("v1/unknown", b"{}") == TokenUsage(0, 0, 0, 0)


def test_endpoint_shapes_registered_for_usage_injection():
    assert endpoint_shapes.get("v1/chat/completions").needs_include_usage is True
    assert endpoint_shapes.get("v1/completions").needs_include_usage is True
