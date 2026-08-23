import asyncio

from llm_manager import bgtask


def test_run_holds_reference_until_done():
    async def main():
        ev = asyncio.Event()
        t = bgtask.run(ev.wait())
        assert t in bgtask._background  # 强引用已登记
        ev.set()
        for _ in range(5):
            await asyncio.sleep(0)
        assert t.done()
        assert t not in bgtask._background  # done 后即移除,不累积

    asyncio.run(main())


def test_run_requires_running_loop():
    import pytest

    c = asyncio.sleep(0)
    with pytest.raises(RuntimeError):
        bgtask.run(c)
    c.close()  # create_task 抛错时协程未被消费;close 防 never-awaited 告警
