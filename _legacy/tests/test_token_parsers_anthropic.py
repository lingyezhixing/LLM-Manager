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
    events = [
        ("message_start", {"type": "message_start",
            "message": {"usage": {"input_tokens": 4, "cache_read_input_tokens": 0, "output_tokens": 0}}}),
        ("message_delta", {"type": "message_delta", "usage": {"output_tokens": 3}}),
        ("message_delta", {"type": "message_delta", "usage": {"output_tokens": 8}}),
    ]
    assert parse_anthropic(_sse(events)) == (4, 8, 0, 4)


def test_anthropic_cache_creation_added_to_prompt_n():
    body = json.dumps({"usage": {"input_tokens": 4, "cache_creation_input_tokens": 10,
                                 "cache_read_input_tokens": 15, "output_tokens": 8}}).encode()
    assert parse_anthropic(body) == (29, 8, 15, 14)


def test_anthropic_via_dispatcher():
    body = json.dumps({"usage": {"input_tokens": 4, "cache_read_input_tokens": 15, "output_tokens": 8}}).encode()
    assert parse_tokens("v1/messages", body) == (19, 8, 15, 4)


def test_anthropic_corrupted_returns_zero():
    assert parse_anthropic(b"garbage{") == (0, 0, 0, 0)
