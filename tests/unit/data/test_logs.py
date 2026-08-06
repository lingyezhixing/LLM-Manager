import asyncio
import threading
import time

import pytest
from llm_manager.data import logs
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
        await logs.flush()  # 落库后广播(广播行=DB 行,带全局 id)
        line = await asyncio.wait_for(q.get(), timeout=1.0)
        received.append(line)
        logs.unsubscribe(sid, q)

    asyncio.run(go())
    rows = logs.log_lines_backfill(db, sid, limit=10)
    assert [r["text"] for r in rows] == ["hello"]
    assert received[0].id == rows[0]["id"]  # 广播行与落库行同一 id
    assert received[0].level == "info"


def test_capture_level_inference(store):
    sid = logs.start_session("model", "m1", "m1")
    logs.capture("m1", "error: nope", "err")
    logs.capture("m1", "server listening", "out")
    asyncio.run(logs.flush())
    rows = logs.log_lines_backfill(store, sid, limit=10)
    assert [(r["text"], r["level"]) for r in rows] == [
        ("error: nope", "error"),
        ("server listening", "ok"),
    ]


def test_session_aliases_new_session(store):
    sid1 = logs.start_session("model", "m1", "m1")
    logs.capture("m1", "old", "out")
    logs.end_session(sid1)
    sid2 = logs.start_session("model", "m1", "m1")
    logs.capture("m1", "new", "out")
    asyncio.run(logs.flush())
    old_rows = logs.log_lines_backfill(store, sid1, limit=10)
    new_rows = logs.log_lines_backfill(store, sid2, limit=10)
    assert [r["text"] for r in old_rows] == ["old"]
    assert [r["text"] for r in new_rows] == ["new"]
    assert new_rows[0]["seq"] == 1


def test_capture_after_end_session_dropped(store):
    sid = logs.start_session("model", "m1", "m1")
    logs.end_session(sid)
    logs.capture("m1", "orphan line", "out")  # 映射已移除 → 丢弃
    asyncio.run(logs.flush())
    assert logs.log_lines_backfill(store, sid, limit=10) == []


def test_system_capture(store):
    sid = logs.start_session("system", None, None)
    logs.capture_system("DEBUG msg", 1000.5)
    logs.capture_system("WARNING msg", 1000.6)
    asyncio.run(logs.flush())
    rows = logs.log_lines_backfill(store, sid, limit=10)
    assert [(r["level"], r["stream"], r["ts"]) for r in rows] == [
        ("info", "sys", 1000.5),
        ("warn", "sys", 1000.6),
    ]


def test_flush_batch_threshold(store):
    sid = logs.start_session("model", "m1", "m1")
    for i in range(5):
        logs.capture("m1", f"l{i}", "out")
    # 未显式 flush 前不落库(阈值 200 未到)
    assert logs.log_lines_backfill(store, sid, limit=10) == []
    asyncio.run(logs.flush())
    assert len(logs.log_lines_backfill(store, sid, limit=10)) == 5


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
    r1 = logs.log_lines_backfill(store, s1, limit=10)
    r2 = logs.log_lines_backfill(store, s2, limit=10)
    assert [(r["seq"], r["text"]) for r in r1] == [(1, "a1"), (2, "a2")]
    assert [(r["seq"], r["text"]) for r in r2] == [(1, "b1")]


def test_subscribe_unknown_session_returns_none(store):
    assert logs.subscribe(9999) is None


def test_flush_no_pending_is_noop(store):
    asyncio.run(logs.flush())  # 不抛


def test_concurrent_flush_preserves_order(store, monkeypatch):
    """并发 flush 严格串行:后到的 flush 等前一个收尾,落库序 == 捕获序(id 单调升)。

    自然调度下写线程先到先得(实测数百次不翻转),竞态难以稳定复现;用 monkeypatch 给
    第一个 insert 注入 50ms 延迟,把"第二个 batch 抢先落库"变成确定性场景——无串行化时
    b 行先落库(ids 1..10)、a 行后落(11..20),断言必失败;有串行化则 t2 等 t1 完成。"""
    sid = logs.start_session("model", "m1", "m1")
    orig_insert = logs.log_insert_lines
    calls = 0

    def slow_insert(db, session_id, rows):
        nonlocal calls
        calls += 1
        if calls == 1:
            time.sleep(0.05)  # 拖延第一个 insert 的锁获取(见上方说明)
        return orig_insert(db, session_id, rows)

    monkeypatch.setattr(logs, "log_insert_lines", slow_insert)

    async def go():
        for i in range(10):
            logs.capture("m1", f"a{i}", "out")
        t1 = asyncio.create_task(logs.flush())  # 快照 a0..a9 并开始落库(被拖延)
        await asyncio.sleep(0)  # 让 t1 真正跑起来(b 行尚未捕获)
        for i in range(10):
            logs.capture("m1", f"b{i}", "out")
        t2 = asyncio.create_task(logs.flush())  # 快照 b0..b9
        await asyncio.gather(t1, t2)

    asyncio.run(go())
    rows = logs.log_lines_backfill(store, sid, limit=100)
    assert [r["text"] for r in rows] == [f"a{i}" for i in range(10)] + [f"b{i}" for i in range(10)]
    ids = [r["id"] for r in rows]
    assert ids == sorted(ids) and len(set(ids)) == 20


def test_auto_flush_threshold_in_loop(store):
    """生产路径:capture 攒满 BATCH_SIZE 自动触发 flush 任务落库(无需显式 flush)。"""
    sid = logs.start_session("model", "m1", "m1")

    async def go():
        for i in range(logs.BATCH_SIZE):
            logs.capture("m1", f"l{i}", "out")
        await asyncio.sleep(0.05)  # 给自动创建的 flush 任务执行时间

    asyncio.run(go())
    rows = logs.log_lines_backfill(store, sid, limit=logs.BATCH_SIZE + 10)
    assert len(rows) == logs.BATCH_SIZE


def test_capture_system_from_worker_thread(store):
    """系统 handler 任意线程 emit:与 flush 并发不丢行、seq 不重复。"""
    sid = logs.start_session("system", None, None)
    errors = []

    def worker():
        try:
            for i in range(500):
                logs.capture_system(f"line {i}", float(i), "INFO")
                if i % 50 == 0:
                    time.sleep(0.0001)
        except Exception as e:
            errors.append(e)

    t = threading.Thread(target=worker)
    t.start()

    async def go():
        for _ in range(20):
            await asyncio.sleep(0.005)
            await logs.flush()

    asyncio.run(go())
    t.join(timeout=5)
    assert not t.is_alive(), "worker 应在 5s 内完成"
    assert not errors
    asyncio.run(logs.flush())  # 兜底:worker 尾部行(与 flush 并发期交错时)
    rows = logs.log_lines_backfill(store, sid, limit=10000)
    assert len(rows) == 500  # 一行不丢(锁正确时)
    assert sorted(r["seq"] for r in rows) == list(range(1, 501))  # seq 无重复(递增持锁)


# ---- log_sessions / log_lines (SQL 存储层测试,自 test_persistence 并入) ----


def test_log_session_crud(tmp_path):
    db = open_db(tmp_path / "t.db")
    sid = logs.log_start_session(db, "system", None, None, 1000.0)
    sid2 = logs.log_start_session(db, "model", "m1", "m1-alias", 2000.0)
    rows = logs.log_sessions(db)
    assert [r["id"] for r in rows] == [sid2, sid]  # 倒序
    assert rows[0]["type"] == "model" and rows[0]["alias"] == "m1-alias"
    logs.log_end_session(db, sid, 1500.0)
    rows = logs.log_sessions(db, type_="system")
    assert rows[0]["end_time"] == 1500.0 and rows[0]["status"] == "ended"


def test_log_lines_insert_and_query(tmp_path):
    db = open_db(tmp_path / "t.db")
    sid = logs.log_start_session(db, "system", None, None, 1000.0)
    ids = logs.log_insert_lines(
        db,
        sid,
        [
            (1, 1000.1, "sys", "info", "boot line"),
            (2, 1000.2, "sys", "warn", "warning"),
            (3, 1000.3, "sys", "error", "boom"),
        ],
    )
    assert len(ids) == 3 and ids[0] < ids[1] < ids[2]
    bf = logs.log_lines_backfill(db, sid, limit=2)
    assert [r["text"] for r in bf] == ["warning", "boom"]
    page = logs.log_lines_before(db, sid, before_id=ids[2], limit=1)
    assert [r["id"] for r in page] == [ids[1]]
    errs = logs.log_lines_backfill(db, sid, limit=10, level="error")
    assert [r["text"] for r in errs] == ["boom"]


def test_log_session_line_count(tmp_path):
    db = open_db(tmp_path / "t.db")
    sid = logs.log_start_session(db, "system", None, None, 1000.0)
    logs.log_insert_lines(db, sid, [(1, 1000.1, "sys", "info", "a")])
    logs.log_insert_lines(db, sid, [(2, 1000.2, "sys", "info", "b")])
    rows = logs.log_sessions(db)
    assert rows[0]["line_count"] == 2


def test_log_insert_lines_empty_returns_empty(tmp_path):
    db = open_db(tmp_path / "t.db")
    assert logs.log_insert_lines(db, 123, []) == []  # 空列表守卫,不触发任何写


def test_log_insert_lines_chunks_large_batches(tmp_path):
    db = open_db(tmp_path / "t.db")
    sid = logs.log_start_session(db, "system", None, None, 1000.0)
    rows = [
        (i, 1000.0 + i, "sys", "info", f"line {i}") for i in range(1, 400)
    ]  # 399 行 → 3 块(150+150+99)
    ids = logs.log_insert_lines(db, sid, rows)
    assert len(ids) == 399
    assert ids == sorted(ids)  # 分块后仍全局自增、保持插入序
    back = logs.log_lines_backfill(db, sid, limit=5000)
    assert [r["text"] for r in back] == [f"line {i}" for i in range(1, 400)]


def test_log_sessions_model_filter_and_before_pagination(tmp_path):
    db = open_db(tmp_path / "t.db")
    s1 = logs.log_start_session(db, "model", "m1", "m1a", 1000.0)
    s2 = logs.log_start_session(db, "model", "m2", "m2a", 2000.0)
    s3 = logs.log_start_session(db, "system", None, None, 3000.0)
    rows = logs.log_sessions(db, model_name="m1")
    assert [r["id"] for r in rows] == [s1]
    rows = logs.log_sessions(db, limit=2)
    assert [r["id"] for r in rows] == [s3, s2]
    rows = logs.log_sessions(db, limit=2, before_id=s3)
    assert [r["id"] for r in rows] == [s2, s1]


def test_log_search_matches_across_sessions_and_filters(tmp_path):
    db = open_db(tmp_path / "t.db")
    s1 = logs.log_start_session(db, "system", None, None, 1000.0)
    s2 = logs.log_start_session(db, "model", "m1", "m1-alias", 2000.0)
    logs.log_insert_lines(db, s1, [(1, 1000.1, "sys", "info", "boot Error")])
    logs.log_insert_lines(db, s2, [(1, 2000.1, "stdout", "warn", "model startup error")])
    total, rows = logs.log_search(db, "error")
    assert total == 2
    assert [r["text"] for r in rows] == [
        "boot Error",
        "model startup error",
    ]  # 跨会话 + ASCII 大小写不敏感
    assert rows[0]["session_type"] == "system" and rows[1]["session_type"] == "model"
    total, rows = logs.log_search(db, "error", session_id=s1)
    assert total == 1 and [r["text"] for r in rows] == ["boot Error"]
    total, rows = logs.log_search(db, "error", type_="model")
    assert total == 1 and [r["text"] for r in rows] == ["model startup error"]
    # 真 total:limit 截断行数但 total 是满足条件的全部匹配数
    total, rows = logs.log_search(db, "error", limit=1)
    assert total == 2 and len(rows) == 1


def test_log_insert_lines_rolls_back_partial_chunks_on_failure(tmp_path):
    """分块插入任一块失败(重复 seq → IntegrityError)→ 整体回滚,不留部分行;
    同连接后续无关 commit 也不得把残留行带落盘。"""
    import sqlite3

    p = tmp_path / "t.db"
    db = open_db(p)
    sid = logs.log_start_session(db, "system", None, None, 1000.0)
    rows = [(i, 1000.0 + i, "sys", "info", f"line {i}") for i in range(1, 201)]  # seq 1..200
    rows.append((151, 3000.0, "sys", "info", "dup seq 151"))  # 201 行 → 第 2 块内重复
    with pytest.raises(sqlite3.IntegrityError):
        logs.log_insert_lines(db, sid, rows)
    logs.log_end_session(db, sid, 5000.0)  # 同连接后续 commit —— 若残留事务会误提交部分行
    db.conn.close()
    db2 = open_db(p)  # 全新连接读盘,验证零泄漏
    assert db2.conn.execute("SELECT COUNT(*) FROM log_lines").fetchone()[0] == 0
    assert db2.conn.execute("SELECT COUNT(*) FROM log_sessions").fetchone()[0] == 1
    assert db2.conn.execute("SELECT end_time FROM log_sessions").fetchone()[0] == 5000.0


def test_log_cleanup_time_and_count(tmp_path):
    """时间规则:now=200000,days=2 → cutoff 27200;全部早于 cutoff → 清光。
    3 个会话:旧系统会话(1000s,3 行)、旧模型会话(1005s,2 行)、新系统会话(5000s,1 行)。"""
    db = open_db(tmp_path / "t.db")
    old_sys = logs.log_start_session(db, "system", None, None, 1000.0)
    logs.log_insert_lines(
        db,
        old_sys,
        [
            (1, 1000.1, "sys", "info", "a"),
            (2, 1000.2, "sys", "info", "b"),
            (3, 1000.3, "sys", "info", "c"),
        ],
    )
    old_mod = logs.log_start_session(db, "model", "m1", "m1", 1005.0)
    logs.log_insert_lines(
        db, old_mod, [(1, 1005.1, "out", "info", "d"), (2, 1005.2, "out", "info", "e")]
    )
    new_sys = logs.log_start_session(db, "system", None, None, 5000.0)
    logs.log_insert_lines(db, new_sys, [(1, 5000.1, "sys", "info", "f")])

    removed_s, removed_l = logs.log_cleanup(db, days=2, count=10, now=200000.0)
    assert removed_s == 3 and removed_l == 6
    assert logs.log_sessions(db) == []
    assert logs.log_lines_backfill(db, old_sys, limit=10) == []


def test_log_cleanup_count_keeps_newest(tmp_path):
    db = open_db(tmp_path / "t.db")
    for i in range(3):
        sid = logs.log_start_session(db, "system", None, None, float(1000 + i))
        logs.log_insert_lines(db, sid, [(1, float(1000 + i) + 0.1, "sys", "info", f"l{i}")])
    removed_s, removed_l = logs.log_cleanup(db, days=9999, count=2, now=10000.0)
    assert removed_s == 1 and removed_l == 1  # 最旧 1 会话(1 行)
    rows = logs.log_sessions(db)
    assert [r["start_time"] for r in rows] == [1002.0, 1001.0]


def test_log_cleanup_both_rules_independent(tmp_path):
    db = open_db(tmp_path / "t.db")
    sid1 = logs.log_start_session(db, "system", None, None, 100.0)  # 超期 且 最旧
    logs.log_insert_lines(db, sid1, [(1, 100.1, "sys", "info", "a")])
    sid2 = logs.log_start_session(db, "system", None, None, 5000.0)  # 不超期
    logs.log_insert_lines(db, sid2, [(1, 5000.1, "sys", "info", "b")])
    removed_s, removed_l = logs.log_cleanup(db, days=1, count=10, now=90000.0)  # 仅时间规则触发
    assert removed_s == 1 and removed_l == 1
    assert [r["id"] for r in logs.log_sessions(db)] == [sid2]


def test_log_cleanup_both_rules_fire_simultaneously(tmp_path):
    """两规则同时触发:时间规则删 {100, 200}(cutoff=3600),条数规则(3>2)补最旧 1 会话(100,
    已含)→ 并集去重 → 删 {100, 200}。"""
    db = open_db(tmp_path / "t.db")
    sid1 = logs.log_start_session(db, "system", None, None, 100.0)  # 超期,且最旧
    logs.log_insert_lines(db, sid1, [(1, 100.1, "sys", "info", "a")])
    sid2 = logs.log_start_session(db, "system", None, None, 200.0)  # 超期
    logs.log_insert_lines(db, sid2, [(1, 200.1, "sys", "info", "b")])
    sid3 = logs.log_start_session(db, "system", None, None, 5000.0)  # 新鲜
    logs.log_insert_lines(db, sid3, [(1, 5000.1, "sys", "info", "c")])
    removed_s, removed_l = logs.log_cleanup(db, days=1, count=2, now=90000.0)
    assert removed_s == 2 and removed_l == 2
    rows = logs.log_sessions(db)
    assert [r["id"] for r in rows] == [sid3]
    assert [r["start_time"] for r in rows] == [5000.0]


def test_log_cleanup_no_doomed_returns_zero(tmp_path):
    """无到期会话且条数未超 → 早退 (0, 0),数据原样。"""
    db = open_db(tmp_path / "t.db")
    sid = logs.log_start_session(db, "system", None, None, 1000.0)
    logs.log_insert_lines(db, sid, [(1, 1000.1, "sys", "info", "a")])
    assert logs.log_cleanup(db, days=9999, count=10, now=10000.0) == (0, 0)
    rows = logs.log_sessions(db)
    assert [r["id"] for r in rows] == [sid]
    assert rows[0]["line_count"] == 1


def test_log_cleanup_chunks_large_doomed_sets(tmp_path):
    """IN 子句按 150 分块:把 SQLITE_LIMIT_VARIABLE_NUMBER 降到 999(模拟 stock CPython
    的编译默认;conda 构建默认 250000 会掩盖该问题)→ >999 会话在册时不触发
    too many SQL variables;行/会话数跨块累计精确。"""
    import sqlite3

    db = open_db(tmp_path / "t.db")
    db.conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
    sids = [logs.log_start_session(db, "system", None, None, float(1000 + i)) for i in range(1000)]
    for sid in sids[:200]:  # 200 会话带行 → 行删除跨 2 块
        logs.log_insert_lines(db, sid, [(1, 1001.0, "sys", "info", "x")])
    removed_s, removed_l = logs.log_cleanup(db, days=2, count=10000, now=200000.0)
    assert removed_s == 1000 and removed_l == 200
    assert logs.log_sessions(db) == []


def test_log_heartbeat_live_writes_end_time(store):
    """心跳把 live_session_ids 中会话的 end_time 推到 now;已结束会话不动。
    运行中标识=内存 _sessions,与 end_time 解耦——心跳直写 end_time 不破坏状态。"""
    live = logs.start_session("model", "m1", "m1")
    ended = logs.start_session("model", "m2", "m2")
    logs.end_session(ended)
    assert logs.log_heartbeat_live(store, 5_000_000_000.0) == 1
    rows = {r["id"]: r for r in logs.log_sessions(store)}
    assert rows[live]["end_time"] == 5_000_000_000.0  # 进行中 → end_time 推到心跳值
    assert rows[ended]["end_time"] is not None  # 已结束保留 end_session 写的精确值
    assert rows[ended]["end_time"] != 5_000_000_000.0  # 不被心跳覆盖


def test_log_sessions_status_uses_live_set(store):
    """status 由内存 live_session_ids 判定,不看 end_time(心跳后 live 会话 end_time 非 NULL 仍 running)。"""
    live = logs.start_session("model", "m1", "m1")
    logs.log_heartbeat_live(store, 5_000_000_000.0)  # live 的 end_time → 非 NULL
    rows = {r["id"]: r for r in logs.log_sessions(store)}
    assert rows[live]["status"] == "running"  # 在 _sessions,虽 end_time 非 NULL
    logs.end_session(live)  # 移出 _sessions
    rows2 = {r["id"]: r for r in logs.log_sessions(store)}
    assert rows2[live]["status"] == "ended"
