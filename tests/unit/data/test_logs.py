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


def test_before_returns_older_page_ascending():
    logs.reset()
    for i in range(10):
        logs.capture("m1", f"line{i}", "out")           # ids 1..10
    # 往前翻页:before id 6,limit 3 → ids [3,4,5](id<6 的最近 3 行,升序)
    page = logs.before("m1", before=6, limit=3)
    assert [ll.id for ll in page] == [3, 4, 5]


def test_before_at_start_returns_empty():
    logs.reset()
    logs.capture("m1", "only", "out")                   # id 1
    assert logs.before("m1", before=1, limit=10) == []


def test_before_respects_level_filter():
    logs.reset()
    logs.capture("m1", "a", "out")                      # id1 info
    logs.capture("m1", "boom error", "err")             # id2 error
    logs.capture("m1", "b", "out")                      # id3 info
    assert [ll.id for ll in logs.before("m1", before=4, limit=10, level="info")] == [1, 3]


def test_search_returns_matching_ids_and_total():
    logs.reset()
    logs.capture("m1", "ctx near limit a", "out")
    logs.capture("m1", "error: x", "err")               # id2
    logs.capture("m1", "ctx near limit b", "out")
    logs.capture("m1", "error: y", "err")               # id4
    res = logs.search("m1", "error")
    assert res.matches == [2, 4]
    assert res.total == 2


def test_search_is_case_insensitive_and_respects_level():
    logs.reset()
    logs.capture("m1", "ERROR boom", "err")             # id1 level=error
    logs.capture("m1", "error in stdout", "out")        # id2 level=info(stream=out)
    assert logs.search("m1", "ERROR").matches == [1, 2]              # 大小写不敏感
    assert logs.search("m1", "error", level="error").matches == [1]  # 叠加 level
