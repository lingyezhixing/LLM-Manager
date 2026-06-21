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
