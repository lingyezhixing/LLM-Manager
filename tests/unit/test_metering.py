from llm_manager.data.metering import parse_tokens, TokenUsage, needs_include_usage


def test_parse_openai_usage_json():
    body = b'{"usage":{"prompt_tokens":100,"completion_tokens":50,"prompt_tokens_details":{"cached_tokens":20}}}'
    assert parse_tokens("v1/chat/completions", body) == TokenUsage(100, 50, 20, 80)


def test_parse_anthropic_non_stream():
    body = b'{"usage":{"input_tokens":40,"cache_read_input_tokens":10,"cache_creation_input_tokens":5,"output_tokens":30}}'
    assert parse_tokens("v1/messages", body) == TokenUsage(55, 30, 10, 45)


def test_parse_unknown_path_returns_zero_and_never_raises():
    assert parse_tokens("v1/whatever", b"not json{{{") == TokenUsage(0, 0, 0, 0)


def test_needs_include_usage_table():
    assert needs_include_usage("v1/chat/completions") is True
    assert needs_include_usage("v1/messages") is False
    assert needs_include_usage("v1/embeddings") is False
