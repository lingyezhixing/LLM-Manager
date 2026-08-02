import asyncio
import time

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


def test_concurrent_flush_preserves_order(store, monkeypatch):
    """并发 flush 严格串行:后到的 flush 等前一个收尾,落库序 == 捕获序(id 单调升)。

    自然调度下写线程先到先得(实测数百次不翻转),竞态难以稳定复现;用 monkeypatch 给
    第一个 insert 注入 50ms 延迟,把"第二个 batch 抢先落库"变成确定性场景——无串行化时
    b 行先落库(ids 1..10)、a 行后落(11..20),断言必失败;有串行化则 t2 等 t1 完成。"""
    sid = logs.start_session("model", "m1", "m1")
    orig_insert = _p.log_insert_lines
    calls = 0

    def slow_insert(db, session_id, rows):
        nonlocal calls
        calls += 1
        if calls == 1:
            time.sleep(0.05)   # 拖延第一个 insert 的锁获取(见上方说明)
        return orig_insert(db, session_id, rows)

    monkeypatch.setattr(_p, "log_insert_lines", slow_insert)
    async def go():
        for i in range(10):
            logs.capture("m1", f"a{i}", "out")
        t1 = asyncio.create_task(logs.flush())   # 快照 a0..a9 并开始落库(被拖延)
        await asyncio.sleep(0)                   # 让 t1 真正跑起来(b 行尚未捕获)
        for i in range(10):
            logs.capture("m1", f"b{i}", "out")
        t2 = asyncio.create_task(logs.flush())   # 快照 b0..b9
        await asyncio.gather(t1, t2)
    asyncio.run(go())
    rows = _p.log_lines_backfill(store, sid, limit=100)
    assert [r["text"] for r in rows] == [f"a{i}" for i in range(10)] + [f"b{i}" for i in range(10)]
    ids = [r["id"] for r in rows]
    assert ids == sorted(ids) and len(set(ids)) == 20


def test_auto_flush_threshold_in_loop(store):
    """生产路径:capture 攒满 BATCH_SIZE 自动触发 flush 任务落库(无需显式 flush)。"""
    sid = logs.start_session("model", "m1", "m1")
    async def go():
        for i in range(logs.BATCH_SIZE):
            logs.capture("m1", f"l{i}", "out")
        await asyncio.sleep(0.05)   # 给自动创建的 flush 任务执行时间
    asyncio.run(go())
    rows = _p.log_lines_backfill(store, sid, limit=logs.BATCH_SIZE + 10)
    assert len(rows) == logs.BATCH_SIZE
