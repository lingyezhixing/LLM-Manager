import asyncio
import sys

from llm_manager.supervisor import Supervisor, ProcessRunner, ProcessRecord


def test_supervisor_implements_process_runner():
    sup = Supervisor()
    assert isinstance(sup, ProcessRunner)


def test_spawn_returns_process_record_and_exits():
    async def main():
        sup = Supervisor()
        # cross-platform trivial command
        cmd = [sys.executable, "-c", "print('hi')"]
        rec = await sup.spawn(cmd, shell=False)
        assert isinstance(rec, ProcessRecord)
        assert rec.pid > 0
        # let it finish + wait-task fire
        await asyncio.sleep(1.0)

    asyncio.run(main())


def test_alive_unknown_pid_is_false():
    sup = Supervisor()
    assert sup.alive(99999999) is False


def test_on_exit_callback_fires_when_process_exits():
    async def main():
        sup = Supervisor()
        seen = []
        # placeholder pid 0 registration (real one set at spawn below)
        sup.on_exit(0, lambda code: seen.append(code))
        cmd = [sys.executable, "-c", "print('hi')"]
        rec = await sup.spawn(cmd, shell=False)
        sup.on_exit(rec.pid, lambda code: seen.append(code))
        await asyncio.sleep(1.0)
        assert seen, "on_exit callback did not fire"
        assert seen[-1] in (0, None)

    asyncio.run(main())


def test_kill_tree_clears_process_tables():
    """#5:kill_tree 后 _procs/_exit_cbs 清(_wait 自清 _wait_tasks),防 start/stop 循环累积 Popen 句柄/内存。"""
    async def main():
        sup = Supervisor()
        rec = await sup.spawn([sys.executable, "-c", "import time; time.sleep(30)"], shell=False)
        sup.on_exit(rec.pid, lambda code: None)
        await asyncio.sleep(0.3)
        assert rec.pid in sup._procs and rec.pid in sup._wait_tasks and rec.pid in sup._exit_cbs
        await sup.kill_tree(rec.pid)
        assert rec.pid not in sup._procs        # kill_tree finally 清
        assert rec.pid not in sup._exit_cbs     # kill_tree finally 清
        await asyncio.sleep(0.5)                # _wait 收尾(popen.wait 返回)+ 自清 _wait_tasks
        assert rec.pid not in sup._wait_tasks

    asyncio.run(main())
