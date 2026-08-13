from llm_manager.data.metering import TokenUsage, needs_include_usage, parse_tokens


def test_parse_openai_usage_json():
    body = b'{"usage":{"prompt_tokens":100,"completion_tokens":50,"prompt_tokens_details":{"cached_tokens":20}}}'
    assert parse_tokens("v1/chat/completions", body) == TokenUsage(100, 50, 20, 80)


def test_parse_anthropic_non_stream():
    body = b'{"usage":{"input_tokens":40,"cache_read_input_tokens":10,"cache_creation_input_tokens":5,"output_tokens":30}}'
    assert parse_tokens("v1/messages", body) == TokenUsage(55, 30, 10, 45)


def test_parse_unknown_path_returns_zero_and_never_raises():
    assert parse_tokens("v1/whatever", b"not json{{{") == TokenUsage(0, 0, 0, 0)


def test_parse_infill_native_non_stream():
    # llama.cpp /infill (TASK_RESPONSE_TYPE_NONE) 非流式:顶层 timings(cache_n/prompt_n/predicted_n)
    body = b'{"index":0,"content":"get_num_threads();\\n","stop":true,"timings":{"cache_n":31,"prompt_n":1,"predicted_n":23}}'
    assert parse_tokens("infill", body) == TokenUsage(32, 23, 31, 1)


def test_parse_infill_native_stream():
    # 流式:末尾 data 块带 timings(中间块无用量,reversed 从末块取)
    body = (
        b'data: {"index":0,"content":"get","stop":false,"tokens_evaluated":32}\n\n'
        b'data: {"index":0,"content":"_num_threads();","stop":false,"tokens_evaluated":32}\n\n'
        b'data: {"index":0,"content":"","stop":true,"timings":{"cache_n":31,"prompt_n":1,"predicted_n":23}}\n\n'
        b"data: [DONE]\n\n"
    )
    assert parse_tokens("infill", body) == TokenUsage(32, 23, 31, 1)


def test_needs_include_usage_table():
    assert needs_include_usage("v1/chat/completions") is True
    assert needs_include_usage("v1/messages") is False
    assert needs_include_usage("v1/embeddings") is False
    assert needs_include_usage("infill") is False  # 原生 timings,无需注入 include_usage


# ---- 通用回退 parse_generic(未知路径自动尝试,按字段存在性分类) ----


def test_generic_fallback_llamacpp_timings():
    body = (
        b'{"content":"get_num_threads();","timings":{"cache_n":31,"prompt_n":1,"predicted_n":23}}'
    )
    assert parse_tokens("infill", body) == TokenUsage(32, 23, 31, 1)


def test_generic_fallback_openai_usage():
    body = b'{"usage":{"prompt_tokens":100,"completion_tokens":50,"prompt_tokens_details":{"cached_tokens":20}}}'
    assert parse_tokens("v1/whatever", body) == TokenUsage(100, 50, 20, 80)


def test_generic_fallback_anthropic_usage():
    body = b'{"usage":{"input_tokens":40,"cache_read_input_tokens":10,"cache_creation_input_tokens":5,"output_tokens":30}}'
    assert parse_tokens("v1/whatever", body) == TokenUsage(55, 30, 10, 45)


def test_generic_fallback_responses_usage():
    # 非流式:usage 在顶层
    body = b'{"object":"response","usage":{"input_tokens":100,"output_tokens":50,"input_tokens_details":{"cached_tokens":20}}}'
    assert parse_tokens("v1/whatever", body) == TokenUsage(100, 50, 20, 80)


def test_generic_fallback_responses_stream_usage():
    # 流式:usage 嵌套在 response.completed 事件的 response 内
    body = (
        b'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
        b'data: {"type":"response.completed","response":{"usage":{"input_tokens":100,"output_tokens":50,"input_tokens_details":{"cached_tokens":20}}}}\n\n'
        b"data: [DONE]\n\n"
    )
    assert parse_tokens("v1/whatever", body) == TokenUsage(100, 50, 20, 80)


def test_generic_fallback_bare_input_output_usage():
    # 无缓存字段 → cache 拆分未知,cache=0、prompt=input(总输入/输出仍明确)
    body = b'{"usage":{"input_tokens":100,"output_tokens":50}}'
    assert parse_tokens("v1/whatever", body) == TokenUsage(100, 50, 0, 100)


def test_generic_fallback_error_body_returns_zero():
    # 无 usage/timings 信号 → 归零,不误记
    assert parse_tokens("v1/whatever", b'{"error":{"message":"bad request"}}') == TokenUsage(
        0, 0, 0, 0
    )


def test_generic_fallback_ollama_non_stream():
    # Ollama /api/generate:prompt_eval_count + eval_count,无缓存字段 → cache=0、prompt=input
    body = b'{"model":"m","response":"hi","done":true,"prompt_eval_count":12,"eval_count":28,"total_duration":1000}'
    assert parse_tokens("api/generate", body) == TokenUsage(12, 28, 0, 12)


def test_generic_fallback_ollama_stream():
    # 流式:中间块无计数,末块(done=true)带计数
    body = (
        b'data: {"model":"m","response":"hi","done":false}\n\n'
        b'data: {"model":"m","response":" you","done":false}\n\n'
        b'data: {"model":"m","response":"","done":true,"prompt_eval_count":12,"eval_count":28}\n\n'
    )
    assert parse_tokens("api/chat", body) == TokenUsage(12, 28, 0, 12)


def test_generic_fallback_gemini_non_stream():
    # Gemini usageMetadata:promptTokenCount/candidatesTokenCount/cachedContentTokenCount
    body = (
        b'{"candidates":[{"content":{"parts":[{"text":"hi"}]}}],'
        b'"usageMetadata":{"promptTokenCount":100,"candidatesTokenCount":50,"cachedContentTokenCount":20}}'
    )
    assert parse_tokens("v1beta/models/m:generateContent", body) == TokenUsage(100, 50, 20, 80)


def test_generic_fallback_gemini_stream():
    # 流式:每块带 usageMetadata,取最末块(累积口径)
    body = (
        b'data: {"candidates":[],"usageMetadata":{"promptTokenCount":90,"candidatesTokenCount":10}}\n\n'
        b'data: {"candidates":[],"usageMetadata":{"promptTokenCount":100,"candidatesTokenCount":50}}\n\n'
    )
    assert parse_tokens("v1beta/models/m:streamGenerateContent", body) == TokenUsage(
        100, 50, 0, 100
    )


def test_generic_fallback_cohere_billed_units():
    # Cohere:meta.billed_units.input_tokens/output_tokens
    body = b'{"text":"hi","meta":{"billed_units":{"input_tokens":30,"output_tokens":15}}}'
    assert parse_tokens("v1/generate", body) == TokenUsage(30, 15, 0, 30)
