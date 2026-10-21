import json

from llm_manager.gateway.stream_capture import (
    StreamTextExtractor,
    elide_media,
    extract_final_text,
    extract_preview,
    sanitize_body,
)


def _sse(*payloads: dict) -> bytes:
    # ensure_ascii=False:让中文字以真实 UTF-8 字节出现,才能测"chunk 切断一个中文字"
    return "".join(f"data: {json.dumps(p, ensure_ascii=False)}\n\n" for p in payloads).encode()


def test_openai_chat_deltas_and_finish_reason():
    ex = StreamTextExtractor("v1/chat/completions")
    out = ex.feed(
        _sse(
            {"choices": [{"delta": {"content": "你"}}]},
            {"choices": [{"delta": {"content": "好"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        )
    )
    assert out == "你好"
    assert ex.finish_reason == "stop"


def test_openai_reasoning_is_marked_and_flagged():
    ex = StreamTextExtractor("v1/chat/completions")
    out = ex.feed(_sse({"choices": [{"delta": {"reasoning_content": "想想"}}]}))
    assert "[思考]" in out and "想想" in out
    assert ex.has_reasoning is True


def test_openai_parallel_tool_calls_do_not_interleave():
    ex = StreamTextExtractor("v1/chat/completions")
    out = (
        ex.feed(
            _sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "a",
                                        "function": {
                                            "name": "get_weather",
                                            "arguments": '{"city":',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 1,
                                        "id": "b",
                                        "function": {"name": "get_time", "arguments": '{"tz":'},
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [{"index": 0, "function": {"arguments": '"Paris"}'}}]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [{"index": 1, "function": {"arguments": '"UTC"}'}}]
                            }
                        }
                    ]
                },
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            )
        )
        + ex.flush()
    )
    assert out.count("[tool:") == 2
    assert '"Paris"}' in out and '"UTC"}' in out
    # 两个调用的参数各自成段，不得出现被切散的交错
    assert out.index("get_weather") < out.index("get_time")
    assert out.index('"Paris"}') < out.index("[tool: get_time]")
    assert ex.has_tool_calls is True


def test_anthropic_blocks_and_stop_reason():
    ex = StreamTextExtractor("v1/messages")
    out = ex.feed(
        b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
        b'"delta":{"type":"text_delta","text":"hi"}}\n\n'
        b'event: content_block_delta\ndata: {"type":"content_block_delta","index":1,'
        b'"delta":{"type":"thinking_delta","thinking":"hmm"}}\n\n'
        b'event: message_delta\ndata: {"type":"message_delta",'
        b'"delta":{"stop_reason":"end_turn"}}\n\n'
    )
    assert "hi" in out and "[思考]" in out
    assert ex.finish_reason == "end_turn"


def test_responses_output_text_delta():
    ex = StreamTextExtractor("v1/responses")
    out = ex.feed(_sse({"type": "response.output_text.delta", "delta": "yo"}))
    assert out == "yo"


def test_utf8_split_across_chunks():
    ex = StreamTextExtractor("v1/chat/completions")
    payload = _sse({"choices": [{"delta": {"content": "汉字"}}]})
    cut = payload.index("汉".encode()) + 1  # 切断一个中文字
    assert ex.feed(payload[:cut]) == ""
    assert ex.feed(payload[cut:]) == "汉字"


def test_partial_sse_line_across_chunks():
    ex = StreamTextExtractor("v1/chat/completions")
    payload = _sse({"choices": [{"delta": {"content": "ok"}}]})
    assert ex.feed(payload[:20]) == ""
    assert ex.feed(payload[20:]) == "ok"


def test_unknown_path_falls_back_to_raw_data_payload():
    ex = StreamTextExtractor("v1/embeddings")
    assert ex.feed(b'data: {"weird": 1}\n\n').strip() == '{"weird": 1}'


def test_never_raises_on_garbage():
    ex = StreamTextExtractor("v1/chat/completions")
    assert ex.feed(b"data: {not json}\n\n") == ""
    assert ex.feed(b"\xff\xfe\x00garbage") == ""


def test_extract_final_text_for_non_stream_shapes():
    chat = json.dumps(
        {"choices": [{"message": {"content": "答案"}, "finish_reason": "stop"}]}
    ).encode()
    r = extract_final_text("v1/chat/completions", chat)
    assert r.text == "答案" and r.finish_reason == "stop"

    anth = json.dumps(
        {"content": [{"type": "text", "text": "甲"}], "stop_reason": "end_turn"}
    ).encode()
    assert extract_final_text("v1/messages", anth).text == "甲"

    resp = json.dumps({"output_text": "乙", "status": "completed"}).encode()
    assert extract_final_text("v1/responses", resp).text == "乙"


def test_extract_preview_takes_last_user_message():
    body = {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "答"},
            {"role": "user", "content": "第二问"},
        ]
    }
    assert extract_preview(body) == "第二问"


def test_extract_preview_joins_text_parts_and_marks_non_text():
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看图"},
                    {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
                ],
            }
        ]
    }
    assert extract_preview(body) == "看图"

    only_image = {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": "https://x/y.png"}}],
            }
        ]
    }
    assert extract_preview(only_image) == "[非文本内容]"


def test_extract_preview_falls_back_for_non_chat_bodies():
    # /v1/responses 用 input,/v1/completions 用 prompt;两者都不该是空预览。
    assert extract_preview({"input": "hello"}) == "hello"
    assert extract_preview({"prompt": "补全这段"}) == "补全这段"
    assert extract_preview({"weird": 1}).startswith("{")


def test_elide_media_keeps_size_and_type():
    out = elide_media("data:image/png;base64," + "A" * 4096)
    assert out.startswith("<已省略") and "image/png" in out


def test_elide_media_handles_data_uri_with_extra_parameters():
    # data:<mime>;charset=utf-8;base64,... —— 常见形态,不能被漏掉
    out = elide_media("data:image/svg+xml;charset=utf-8;base64," + "A" * 200_000)
    assert out.startswith("<已省略") and "svg" in out


def test_elide_media_keeps_remote_url():
    url = "https://example.com/a.png"
    assert elide_media(url) == url


def test_sanitize_body_deep_copies_and_elides_known_keys():
    body = {
        "model": "m1",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64," + "B" * 4096},
                    }
                ],
            }
        ],
    }
    out = sanitize_body(body)
    assert "base64" not in json.dumps(out)
    assert "<已省略" in json.dumps(out, ensure_ascii=False)
    # 原 body 必须原封不动
    assert "base64" in json.dumps(body)
