import asyncio
import json

from llm_manager.data import logs
from llm_manager.gateway.api.models import _logs_stream


def test_logs_stream_backfills_then_streams():
    logs.reset()
    logs.capture("m1", "old1", "out")
    logs.capture("m1", "old2", "out")
    logs.capture("m1", "error: boom", "err")

    async def go():
        out = []
        gen = _logs_stream("m1", limit=10)
        async for frame in gen:
            out.append(json.loads(frame.removeprefix("data: ").strip()))
            if len(out) == 3:        # 取完回填 3 行即停(真端点无限)
                break
        await gen.aclose()           # 触发 finally → unsubscribe
        return out

    res = asyncio.run(go())
    assert [ll["text"] for ll in res] == ["old1", "old2", "error: boom"]
    assert res[0]["level"] == "info" and res[2]["level"] == "error"
    assert res[0]["id"] == 1 and res[2]["id"] == 3


def test_logs_stream_respects_level_filter_on_backfill():
    logs.reset()
    logs.capture("m1", "info line", "out")
    logs.capture("m1", "error: x", "err")
    logs.capture("m1", "info line2", "out")

    async def go():
        out = []
        gen = _logs_stream("m1", limit=10, level="error")
        async for frame in gen:
            out.append(json.loads(frame.removeprefix("data: ").strip()))
            if len(out) == 1:
                break
        await gen.aclose()
        return out

    res = asyncio.run(go())
    assert len(res) == 1 and res[0]["text"] == "error: x" and res[0]["level"] == "error"
