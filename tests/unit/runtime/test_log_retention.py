import asyncio

import pytest

from llm_manager.data import logs as _logs
from llm_manager.data.persistence import open_db
from llm_manager.runtime import background as log_retention


@pytest.fixture
def db(tmp_path):
    return open_db(tmp_path / "t.db")


def _seed(db):
    sid = _logs.log_start_session(db, "system", None, None, 1000.0)  # 超期
    _logs.log_insert_lines(db, sid, [(1, 1000.1, "sys", "info", "old")])
    sid2 = _logs.log_start_session(db, "model", "m1", "m1", 5000.0)  # 新
    _logs.log_insert_lines(db, sid2, [(1, 5000.1, "out", "info", "new")])
    return sid, sid2


def test_loop_cleans_by_time(db):
    _old, new = _seed(db)
    stop = asyncio.Event()

    async def go():
        # 时间规则 cutoff = now - days*86400:now=2_595_000 → cutoff=3_000,
        # 老会话(1000)超期、新会话(5000)不超期,正好测时间规则单独触发
        loop = asyncio.create_task(
            log_retention.log_retention_loop(
                db, lambda: (30, 10), stop, period=0.05, now=2_595_000.0
            )
        )
        await asyncio.sleep(0.15)  # 至少一轮
        stop.set()
        await asyncio.wait_for(loop, timeout=1.0)

    asyncio.run(go())
    rows = _logs.log_sessions(db)
    assert [r["id"] for r in rows] == [new]


def test_loop_count_rule(db):
    _seed(db)
    stop = asyncio.Event()

    async def go():
        loop = asyncio.create_task(
            log_retention.log_retention_loop(db, lambda: (9999, 1), stop, period=0.05, now=6000.0)
        )
        await asyncio.sleep(0.15)
        stop.set()
        await asyncio.wait_for(loop, timeout=1.0)

    asyncio.run(go())
    rows = _logs.log_sessions(db)
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
        loop = asyncio.create_task(
            log_retention.log_retention_loop(db, flaky, stop, period=0.02, now=6000.0)
        )
        await asyncio.sleep(0.1)
        stop.set()
        await asyncio.wait_for(loop, timeout=1.0)

    asyncio.run(go())
    assert calls[0] >= 2  # 第二轮跑到了


def test_loop_reads_fresh_settings_each_round(db):
    """每轮 fresh 读规则:前两轮读旧值(不清理),改配置后下一轮立即按新值执行 count 规则。"""
    _seed(db)
    stop = asyncio.Event()
    settings = [(9999, 99)]  # 旧值:count 足够大,不清理
    calls = [0]
    second_read = asyncio.Event()  # 第二轮已读到旧值
    new_read = asyncio.Event()  # 有轮读到了新值

    def getter():
        calls[0] += 1
        if calls[0] == 2:
            second_read.set()
        if settings[0] != (9999, 99):
            new_read.set()
        return settings[0]

    async def go():
        loop = asyncio.create_task(
            log_retention.log_retention_loop(db, getter, stop, period=0.02, now=6000.0)
        )
        await asyncio.wait_for(second_read.wait(), timeout=1.0)
        assert len(_logs.log_sessions(db)) == 2  # 旧值两轮确实未清理
        settings[0] = (9999, 1)  # 换新规则
        await asyncio.wait_for(new_read.wait(), timeout=1.0)
        stop.set()
        await asyncio.wait_for(loop, timeout=1.0)  # 等新值那轮的清理收尾

    asyncio.run(go())
    rows = _logs.log_sessions(db)
    assert len(rows) == 1 and rows[0]["start_time"] == 5000.0


def test_loop_disabled_gate(db):
    """days=0 → 循环照跑但不清(守卫 days>0 and count>0)。种子会话已超期。"""
    _seed(db)
    stop = asyncio.Event()

    async def go():
        loop = asyncio.create_task(
            log_retention.log_retention_loop(
                db, lambda: (0, 10), stop, period=0.05, now=2_595_000.0
            )
        )
        await asyncio.sleep(0.15)  # 至少一轮(超期会话仍在)
        stop.set()
        await asyncio.wait_for(loop, timeout=1.0)

    asyncio.run(go())
    assert len(_logs.log_sessions(db)) == 2
