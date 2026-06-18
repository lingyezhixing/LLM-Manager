import json
from core.api_router import TokenTracker


def _make_tracker():
    # TokenTracker(monitor, config_manager) — extraction does not touch these.
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
