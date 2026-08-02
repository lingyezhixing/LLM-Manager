import asyncio
import pytest
from llm_manager.data.persistence import open_db
from llm_manager.data import persistence as _p
from llm_manager.runtime import log_retention


@pytest.fixture
def db(tmp_path):
    return open_db(tmp_path / "t.db")


def _seed(db):
    sid = _p.log_start_session(db, "system", None, None, 1000.0)   # 超期
    _p.log_insert_lines(db, sid, [(1, 1000.1, "sys", "info", "old")])
    sid2 = _p.log_start_session(db, "model", "m1", "m1", 5000.0)   # 新
    _p.log_insert_lines(db, sid2, [(1, 5000.1, "out", "info", "new")])
    return sid, sid2


def test_retention_settings_defaults(db):
    days, count = log_retention.retention_settings(db)
    assert days == 30 and count == 10


def test_retention_settings_reads_db(db):
    from llm_manager.data.config_store import set_setting
    set_setting(db, "log_retention_days", "7")
    set_setting(db, "log_retention_count", "3")
    days, count = log_retention.retention_settings(db)
    assert (days, count) == (7, 3)


def test_loop_cleans_by_time(db):
    old, new = _seed(db)
    stop = asyncio.Event()

    async def go():
        # 时间规则 cutoff = now - days*86400:now=2_595_000 → cutoff=3_000,
        # 老会话(1000)超期、新会话(5000)不超期,正好测时间规则单独触发
        loop = asyncio.create_task(log_retention.log_retention_loop(
            db, lambda: (30, 10), stop, period=0.05, now=2_595_000.0))
        await asyncio.sleep(0.15)      # 至少一轮
        stop.set()
        await asyncio.wait_for(loop, timeout=1.0)
    asyncio.run(go())
    rows = _p.log_sessions(db)
    assert [r["id"] for r in rows] == [new]


def test_loop_count_rule(db):
    _seed(db)
    stop = asyncio.Event()

    async def go():
        loop = asyncio.create_task(log_retention.log_retention_loop(
            db, lambda: (9999, 1), stop, period=0.05, now=6000.0))
        await asyncio.sleep(0.15)
        stop.set()
        await asyncio.wait_for(loop, timeout=1.0)
    asyncio.run(go())
    rows = _p.log_sessions(db)
    assert len(rows) == 1 and rows[0]["start_time"] == 5000.0


def test_loop_exception_does_not_kill_loop(db):
    """get_settings 抛错 → 记日志继续下一轮(不中断循环)。"""
    calls = [0]

    def flaky():
        calls[0] += 1
        if calls[0] == 1:
            raise RuntimeError("boom")
        return (30, 10)

    stop = asyncio.Event()

    async def go():
        loop = asyncio.create_task(log_retention.log_retention_loop(
            db, flaky, stop, period=0.02, now=6000.0))
        await asyncio.sleep(0.1)
        stop.set()
        await asyncio.wait_for(loop, timeout=1.0)
    asyncio.run(go())
    assert calls[0] >= 2      # 第二轮跑到了
