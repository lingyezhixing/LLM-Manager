"""tick_loop 骨架直接单测:wait_first / on_error / 可中断睡眠语义。

三个后台循环(心跳/日志保留/空闲回收)共用该骨架,此前仅被间接覆盖;
此测试直接钉死其控制流契约(wait_first 先睡、on_error 兜底继续、无 on_error 则抛)。"""

import asyncio

import pytest

from llm_manager.runtime.loops import tick_loop


def test_wait_first_delays_first_tick():
    async def main():
        stop = asyncio.Event()
        calls: list[int] = []

        async def on_tick():
            calls.append(1)

        task = asyncio.create_task(tick_loop(stop, 10, on_tick, wait_first=True))
        await asyncio.sleep(0.05)
        assert calls == []  # 先睡一轮,尚未 tick
        stop.set()
        await task
        assert calls == []  # 首轮前被停,一次都不跑

    asyncio.run(main())


def test_immediate_first_tick_when_not_wait_first():
    async def main():
        stop = asyncio.Event()
        calls: list[int] = []

        async def on_tick():
            calls.append(1)

        task = asyncio.create_task(tick_loop(stop, 10, on_tick))
        await asyncio.sleep(0.05)
        assert len(calls) >= 1  # 立即首轮
        stop.set()
        await task

    asyncio.run(main())


def test_stop_event_interrupts_long_sleep():
    async def main():
        stop = asyncio.Event()
        calls: list[int] = []

        async def on_tick():
            calls.append(1)

        task = asyncio.create_task(tick_loop(stop, 999, on_tick))
        await asyncio.sleep(0.05)
        assert calls == [1]
        stop.set()
        await asyncio.wait_for(task, timeout=1)  # 睡眠被中断,快速退出而非等满 period

    asyncio.run(main())


def test_on_error_catches_and_keeps_looping():
    async def main():
        stop = asyncio.Event()
        errors: list[Exception] = []
        calls: list[int] = []

        async def on_tick():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")

        task = asyncio.create_task(tick_loop(stop, 0.01, on_tick, on_error=errors.append))
        while len(calls) < 2:  # 首轮异常被兜底,第二轮照常执行
            await asyncio.sleep(0.01)
        stop.set()
        await task
        assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
        assert len(calls) >= 2

    asyncio.run(main())


def test_error_propagates_without_on_error():
    async def main():
        stop = asyncio.Event()

        async def on_tick():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await tick_loop(stop, 0.01, on_tick)  # 无兜底 → 异常上抛、循环终止

    asyncio.run(main())
