import asyncio
import pytest
from llm_manager.data import logs, persistence as _p
from llm_manager.data.persistence import open_db


@pytest.fixture
def store(tmp_path):
    db = open_db(tmp_path / "t.db")
    logs.init(db)
    yield db
    logs.reset()


def test_infer_level():
    assert logs.infer_level("server listening on :10005", "out") == "ok"
    assert logs.infer_level("loading weights", "out") == "info"
    assert logs.infer_level("some warning text", "err") == "warn"
    assert logs.infer_level("error: boom", "err") == "error"
    assert logs.infer_level("Traceback (most recent call)", "err") == "error"


def test_system_level_normalized():
    assert logs.system_level("DEBUG") == "info"
    assert logs.system_level("INFO") == "info"
    assert logs.system_level("WARNING") == "warn"
    assert logs.system_level("ERROR") == "error"
    assert logs.system_level("CRITICAL") == "error"


def test_capture_flush_persists_and_broadcasts(store):
    db = store
    sid = logs.start_session("model", "m1", "m1-alias")
    received = []

    async def go():
        q = logs.subscribe(sid)
        logs.capture("m1", "hello", "out")
        await logs.flush()                     # 落库后广播(广播行=DB 行,带全局 id)
        line = await asyncio.wait_for(q.get(), timeout=1.0)
        received.append(line)
        logs.unsubscribe(sid, q)

    asyncio.run(go())
    rows = _p.log_lines_backfill(db, sid, limit=10)
    assert [r["text"] for r in rows] == ["hello"]
    assert received[0].id == rows[0]["id"]     # 广播行与落库行同一 id
    assert received[0].level == "info"


def test_capture_level_inference(store):
    sid = logs.start_session("model", "m1", "m1")
    logs.capture("m1", "error: nope", "err")
    logs.capture("m1", "server listening", "out")
    asyncio.run(logs.flush())
    rows = _p.log_lines_backfill(store, sid, limit=10)
    assert [(r["text"], r["level"]) for r in rows] == [("error: nope", "error"), ("server listening", "ok")]


def test_session_aliases_new_session(store):
    sid1 = logs.start_session("model", "m1", "m1")
    logs.capture("m1", "old", "out")
    logs.end_session(sid1)
    sid2 = logs.start_session("model", "m1", "m1")
    logs.capture("m1", "new", "out")
    asyncio.run(logs.flush())
    old_rows = _p.log_lines_backfill(store, sid1, limit=10)
    new_rows = _p.log_lines_backfill(store, sid2, limit=10)
    assert [r["text"] for r in old_rows] == ["old"]
    assert [r["text"] for r in new_rows] == ["new"]
    assert new_rows[0]["seq"] == 1


def test_capture_after_end_session_dropped(store):
    sid = logs.start_session("model", "m1", "m1")
    logs.end_session(sid)
    logs.capture("m1", "orphan line", "out")   # 映射已移除 → 丢弃
    asyncio.run(logs.flush())
    assert _p.log_lines_backfill(store, sid, limit=10) == []


def test_system_capture(store):
    sid = logs.start_session("system", None, None)
    logs.capture_system("DEBUG msg", 1000.5)
    logs.capture_system("WARNING msg", 1000.6)
    asyncio.run(logs.flush())
    rows = _p.log_lines_backfill(store, sid, limit=10)
    assert [(r["level"], r["stream"], r["ts"]) for r in rows] == [("info", "sys", 1000.5), ("warn", "sys", 1000.6)]


def test_flush_batch_threshold(store):
    sid = logs.start_session("model", "m1", "m1")
    for i in range(5):
        logs.capture("m1", f"l{i}", "out")
    # 未显式 flush 前不落库(阈值 200 未到)
    assert _p.log_lines_backfill(store, sid, limit=10) == []
    asyncio.run(logs.flush())
    assert len(_p.log_lines_backfill(store, sid, limit=10)) == 5


def test_current_system_session(store):
    sid = logs.start_session("system", None, None)
    assert logs.current_system_session_id() == sid


def test_flush_grouped_by_session(store):
    """多会话并发攒批:flush 按 session 分组落库,seq 与行归属正确。"""
    s1 = logs.start_session("model", "m1", "m1")
    s2 = logs.start_session("model", "m2", "m2")
    logs.capture("m1", "a1", "out")
    logs.capture("m2", "b1", "out")
    logs.capture("m1", "a2", "out")
    asyncio.run(logs.flush())
    r1 = _p.log_lines_backfill(store, s1, limit=10)
    r2 = _p.log_lines_backfill(store, s2, limit=10)
    assert [(r["seq"], r["text"]) for r in r1] == [(1, "a1"), (2, "a2")]
    assert [(r["seq"], r["text"]) for r in r2] == [(1, "b1")]


def test_subscribe_unknown_session_returns_none(store):
    assert logs.subscribe(9999) is None


def test_flush_no_pending_is_noop(store):
    asyncio.run(logs.flush())   # 不抛
