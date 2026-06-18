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
    # lmdeploy emits response.incomplete (truncation) — must ALSO be matched, not just completed
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
