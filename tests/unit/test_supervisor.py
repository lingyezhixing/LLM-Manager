import asyncio
import sys

from llm_manager.supervisor import ProcessRecord, ProcessRunner, Supervisor


def test_supervisor_implements_process_runner():
    sup = Supervisor()
    assert isinstance(sup, ProcessRunner)


def test_spawn_returns_process_record_and_exits():
    async def main():
        sup = Supervisor()
        # cross-platform trivial command
        cmd = [sys.executable, "-c", "print('hi')"]
        rec = await sup.spawn(cmd)
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
        rec = await sup.spawn(cmd)
        sup.on_exit(rec.pid, lambda code: seen.append(code))
        await asyncio.sleep(1.0)
        assert seen, "on_exit callback did not fire"
        assert seen[-1] in (0, None)

    asyncio.run(main())


def test_on_exit_late_registration_after_cleanup_is_noop():
    """/#3 迟注册(进程已退出、表已清)不得重建《永清条目》:on_exit 幂等拒绝,否则
    _exit_cbs 中有永不触发回调的键,且 kill_tree/_wait 的清表逻辑被绕开。"""

    seen = []

    async def main():
        sup = Supervisor()
        rec = await sup.spawn([sys.executable, "-c", "pass"])
        for _ in range(100):  # 等自然退出 + 表清理
            if rec.pid not in sup._procs:
                break
            await asyncio.sleep(0.02)
        assert rec.pid not in sup._procs  # 已清理
        sup.on_exit(rec.pid, lambda c: seen.append(c))
        assert rec.pid not in sup._exit_cbs  # 迟到注册被拒绝

    asyncio.run(main())
    assert seen == []


def test_kill_tree_blocking_sync_runs_in_thread():
    """#5 kill_tree 的 psutil 同步段(枚举 + wait_procs ≤3s)必须在 asyncio.to_thread
    执行:若直接跑在协程体内,阻塞期间事件循环冻结(心跳/idle/日志广播全部停滞)。"""

    async def main():
        import time as _t

        from llm_manager import supervisor as _sup

        class FakeProc:
            def __init__(self, pid):
                self._pid = pid

            def children(self, recursive=True):
                return []

            def kill(self):
                pass

        def fake_wait_procs(procs, timeout=None):
            for _ in range(60):  # 模拟 0.3s 阻塞(> 多个 tick 周期)
                _t.sleep(0.005)
            return [procs[0]], []

        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)

        orig_process, orig_wait = _sup.psutil.Process, _sup.psutil.wait_procs
        try:
            _sup.psutil.Process = FakeProc
            _sup.psutil.wait_procs = fake_wait_procs
            sup = Supervisor()
            tick_task = asyncio.create_task(ticker())
            await asyncio.sleep(0.05)
            before = ticks
            assert await sup.kill_tree(12345)
            synced_ticks = ticks - before
            tick_task.cancel()
        finally:
            _sup.psutil.Process = orig_process
            _sup.psutil.wait_procs = orig_wait

        assert synced_ticks >= 10  # 阻塞期间 loop 持续推进(修复前被冻结 ≈ 0)

    asyncio.run(main())


def test_kill_tree_clears_process_tables():
    """#5:kill_tree 后 _procs/_exit_cbs 清(_wait 自清 _wait_tasks),防 start/stop 循环累积 Popen 句柄/内存。"""

    async def main():
        sup = Supervisor()
        rec = await sup.spawn([sys.executable, "-c", "import time; time.sleep(30)"])
        sup.on_exit(rec.pid, lambda code: None)
        await asyncio.sleep(0.3)
        assert rec.pid in sup._procs and rec.pid in sup._wait_tasks and rec.pid in sup._exit_cbs
        await sup.kill_tree(rec.pid)
        assert rec.pid not in sup._procs  # kill_tree finally 清
        assert rec.pid not in sup._exit_cbs  # kill_tree finally 清
        await asyncio.sleep(0.5)  # _wait 收尾(popen.wait 返回)+ 自清 _wait_tasks
        assert rec.pid not in sup._wait_tasks

    asyncio.run(main())


def test_spawn_captures_stdout_and_stderr_via_on_output():
    received = []

    async def go():
        sup = Supervisor()

        def on_output(line, stream):
            received.append((line, stream))

        # 用 chr(10) 生成换行,避免不同 shell (MSYS/Git Bash/POSIX) 对 \n 转义的解析差异。
        cmd = 'python -c "import sys; print(\\"out-line\\"); sys.stderr.write(\\"err-line\\" + chr(10)); sys.stderr.flush()"'
        rec = await sup.spawn(cmd, on_output=on_output)
        await sup._wait_tasks[rec.pid]  # wait for process exit → reader EOF
        await asyncio.sleep(0.05)  # let call_soon_threadsafe callbacks land

    asyncio.run(go())
    assert ("out-line", "out") in received
    assert ("err-line", "err") in received


def test_natural_exit_cleans_all_tables():
    """自然退出:_wait 路径清空 _procs/_exit_cbs/_readers/_wait_tasks(修复累积泄漏)。"""

    async def main():
        sup = Supervisor()
        exited = asyncio.Event()
        proc = await sup.spawn([sys.executable, "-c", "pass"], on_output=lambda _line, _s: None)
        assert proc.pid in sup._readers
        assert proc.pid in sup._procs
        sup.on_exit(proc.pid, lambda _rc: exited.set())
        await asyncio.wait_for(exited.wait(), timeout=5)
        for _ in range(100):  # _wait 清理在回调后执行,轮询等收敛
            if not sup._procs and not sup._readers and not sup._wait_tasks:
                break
            await asyncio.sleep(0.02)
        assert sup._procs == {}
        assert sup._exit_cbs == {}
        assert sup._readers == {}
        assert sup._wait_tasks == {}

    asyncio.run(main())


def test_kill_cleans_all_tables():
    """kill 路径:kill_tree finally 清空 _procs/_exit_cbs/_readers(修复累积泄漏)。"""

    async def main():
        sup = Supervisor()
        proc = await sup.spawn(
            [sys.executable, "-c", "import time; time.sleep(60)"], on_output=lambda _line, _s: None
        )
        assert proc.pid in sup._readers
        assert proc.pid in sup._procs
        assert await sup.kill_tree(proc.pid)
        assert sup._procs == {}
        assert sup._exit_cbs == {}
        assert sup._readers == {}

    asyncio.run(main())


def test_fast_kill_does_not_leak_wait_tasks():
    """spawn→立即 kill:_wait 早退路径自清 _wait_tasks(修复快杀循环残留)。"""

    async def main():
        sup = Supervisor()
        for _ in range(3):
            proc = await sup.spawn([sys.executable, "-c", "import time; time.sleep(60)"])
            assert await sup.kill_tree(proc.pid)
        for _ in range(100):  # _wait 早退在 kill_tree 同步 finally 后执行,轮询等收敛
            if not sup._wait_tasks:
                break
            await asyncio.sleep(0.02)
        assert sup._wait_tasks == {}
        assert sup._procs == {}
        assert sup._readers == {}

    asyncio.run(main())
