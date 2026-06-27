import asyncio
from llm_manager.data import logs

def test_infer_level():
    assert logs.infer_level("server listening on :10005", "out") == "ok"
    assert logs.infer_level("loading weights", "out") == "info"
    assert logs.infer_level("some warning text", "err") == "warn"
    assert logs.infer_level("error: boom", "err") == "error"
    assert logs.infer_level("Traceback (most recent call)", "err") == "error"

def test_capture_appends_and_backfills():
    logs.reset()
    logs.capture("m1", "first line", "out")
    logs.capture("m1", "server listening", "out")
    logs.capture("m1", "error: nope", "err")
    bf = logs.backfill("m1", limit=10)
    assert [ll.text for ll in bf] == ["first line", "server listening", "error: nope"]
    assert [ll.level for ll in bf] == ["info", "ok", "error"]
    assert bf[-1].id == 3 and bf[0].id == 1

def test_backfill_level_filter_and_limit():
    logs.reset()
    for i in range(5):
        logs.capture("m2", f"line{i}", "out")
    logs.capture("m2", "error: x", "err")
    assert [ll.text for ll in logs.backfill("m2", limit=2)] == ["line4", "error: x"]
    assert [ll.text for ll in logs.backfill("m2", limit=10, level="error")] == ["error: x"]

def test_capture_publishes_to_subscribers():
    logs.reset()
    async def go():
        q = logs.subscribe("m1")
        logs.capture("m1", "hello", "out")
        line = await asyncio.wait_for(q.get(), timeout=1.0)
        logs.unsubscribe("m1", q)
        return line
    line = asyncio.run(go())
    assert line.text == "hello" and line.stream == "out"

def test_session_reset_on_new_spawn():
    logs.reset()
    logs.capture("m1", "old session", "out")
    logs.end_session("m1")
    logs.capture("m1", "new session", "out")
    bf = logs.backfill("m1", limit=10)
    assert len(bf) == 1 and bf[0].id == 1 and bf[0].text == "new session"
