"""回归:retention 删除 live 会话 DB 行 与 flush 的相互防御。

- flush 遇会话落库失败(FK:会话行已被 retention 删除)→ 丢会话不炸管线;
- log_cleanup 的 live_session_ids 排除参数(两个规则都生效);
- retention 循环把模块级 live 会话 id 传给 log_cleanup(wiring 回归)。
"""

import asyncio

from llm_manager.data import logs
from llm_manager.data.persistence import open_db
from llm_manager.runtime import background as log_retention


def test_flush_survives_deleted_session(tmp_path):
    """live 会话的 DB 行被 retention 删掉 → flush 落库 FK 失败:
    丢弃该会话(warning)继续,不抛异常;管线后续照常工作。"""
    db = open_db(tmp_path / "t.db")
    logs.init(db)
    try:
        sid = logs.start_session("system", None, None, start=1000.0)  # live(最旧)
        for i in range(12):
            logs.log_start_session(db, "model", f"m{i}", f"m{i}", float(2000 + i))
        removed_s, removed_l = logs.log_cleanup(db, days=9999, count=10, now=99999.0)
        assert sid not in [r["id"] for r in logs.log_sessions(db, live_ids=None)]  # live 行已被删
        assert removed_s == 3 and removed_l == 0

        logs.capture_system("x", 1.0, "INFO")  # 排入死会话行
        asyncio.run(logs.flush())  # 不抛
        assert logs.subscribe(sid) is None  # 会话已被丢弃
        assert logs.current_system_session_id() is None

        # 管线存活:新会话 capture+flush 正常落库,无死会话的残留/半截行
        sid2 = logs.start_session("model", "m2", "m2")
        logs.capture("m2", "alive", "out")
        asyncio.run(logs.flush())
        rows = logs.log_lines_backfill(db, sid2, limit=10)
        assert [r["text"] for r in rows] == ["alive"]
        assert db.conn.execute("SELECT COUNT(*) FROM log_lines").fetchone()[0] == 1
    finally:
        logs.reset()
        db.conn.close()


def test_log_cleanup_skips_live_sessions(tmp_path):
    """count 规则排除 live_session_ids:live 会话行幸存,最旧结束会话被删。"""
    db = open_db(tmp_path / "t.db")
    live = logs.log_start_session(db, "system", None, None, 1000.0)
    for i in range(12):
        logs.log_start_session(db, "model", f"m{i}", f"m{i}", float(2000 + i))
    removed_s, removed_l = logs.log_cleanup(db, days=9999, count=10, now=99999.0, live_ids={live})
    rows = [r["id"] for r in logs.log_sessions(db, live_ids=None)]
    assert live in rows
    assert sorted(rows) == [live] + list(range(4, 14))  # 删了最旧 3 个中的 2 个(非 live)
    assert (removed_s, removed_l) == (2, 0)
    db.conn.close()


def test_log_cleanup_time_rule_skips_live(tmp_path):
    """时间规则同样排除 live_session_ids(belt-and-braces 两规则都生效)。"""
    db = open_db(tmp_path / "t.db")
    live = logs.log_start_session(db, "system", None, None, 1000.0)
    logs.log_start_session(db, "model", "m", "m", 1005.0)  # 旧会话(被时间规则删除)
    removed_s, _ = logs.log_cleanup(
        db,
        days=2,
        count=100,
        now=200000.0,  # cutoff=27200
        live_ids={live},
    )
    assert [r["id"] for r in logs.log_sessions(db, live_ids=None)] == [live]
    assert removed_s == 1
    db.conn.close()


def test_loop_skips_live_sessions_in_module(tmp_path):
    """retention 循环把模块级 live 会话 id 传给 log_cleanup(wiring 回归)。
    若 wiring 被移除,count 规则会删掉 live 会话的 DB 行 → 后续 flush 落库 FK 失败。"""
    db = open_db(tmp_path / "t.db")
    logs.init(db)
    try:
        logs.start_session("system", None, None, start=1000.0)  # live 会话(下方 id 1)
        for i in range(5):
            logs.log_start_session(db, "model", f"m{i}", f"m{i}", float(2000 + i))
        stop = asyncio.Event()

        async def go():
            loop = asyncio.create_task(
                log_retention.log_retention_loop(
                    db, lambda: (9999, 1), stop, period=0.05, now=99999.0
                )
            )
            await asyncio.sleep(0.15)
            stop.set()
            await asyncio.wait_for(loop, timeout=1.0)

        asyncio.run(go())
        # 6 会话、count=1 → 删最旧 5 个(id 1..5):live(id 1)被排除幸存,
        # 最新结束会话(id 6)按规则幸存。无 wiring 时 live 会被删 → 回归可测。
        assert sorted(r["id"] for r in logs.log_sessions(db, live_ids=None)) == [1, 6]
    finally:
        logs.reset()
        db.conn.close()
