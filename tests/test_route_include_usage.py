def test_route_request_injects_include_usage_for_streaming_chat():
    """Source-level guard: route_request must inject stream_options.include_usage
    for streaming v1/chat/completions + v1/completions (lmdeploy needs it)."""
    import os
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(repo, "core", "api_router.py"), encoding="utf-8").read()
    start = src.index("async def route_request")
    body = src[start:]
    assert "include_usage" in body, "route_request must inject stream_options.include_usage"
    assert "setdefault" in body, "must use setdefault so client's existing stream_options are preserved"
    assert "v1/chat/completions" in body and "v1/completions" in body, "must scope injection to chat/completions paths"
