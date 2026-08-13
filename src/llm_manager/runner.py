"""Entrypoint / parent supervisor (class NapCat).

``python -m llm_manager`` = parent supervisor (resident, touches no DB, holds no
app state): spawns ``python -m llm_manager --worker`` (= worker, runs create_app
+ server.run). Worker exit 81 → parent spawns a fresh worker (each run is a new
process → OS reclaims everything); 0 → parent exits; other (crash) → parent also
exits, no self-heal (visible failure). Strict ordering: parent waits for the worker
rc before spawning the next → no dual workers, no port contention.

Signal forwarding: parent receives Ctrl-C/SIGTERM → forwards to the worker process
group (Win CTRL_BREAK_EVENT / POSIX killpg SIGTERM) for graceful shutdown;
``_SHUTDOWN_GRACE`` timeout then force-kills as a backstop.

dev (``uvicorn --factory --reload``) bypasses main entirely; the restart endpoint's
no-server branch os._exit(81) directly (dev is one-shot)."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from llm_manager import RESTART_EXIT_CODE
from llm_manager.supervisor import process_group_kwargs

logger = logging.getLogger(__name__)

_WORKER_FLAG = "--worker"
_SHUTDOWN_GRACE = 10.0  # worker 优雅关闭超时(秒);超时强杀,防卡死拽死 parent


def exit_code_for(restart_requested: bool) -> int:
    """worker 退出码:restart_requested → 哨兵码(parent 监督器在其上拉新 worker),否则 0(正常退出)。"""
    return RESTART_EXIT_CODE if restart_requested else 0


def _should_respawn(rc: int | None) -> bool:
    """parent 决策:worker 退出码 → 是否拉新 worker。81=重启→True;其余(0 正常/崩溃)→False。"""
    return rc == RESTART_EXIT_CODE


def _worker_command() -> list[str]:
    """worker 子进程命令:同解释器跑 `python -m llm_manager --worker`。"""
    return [sys.executable, "-m", "llm_manager", _WORKER_FLAG]


def _spawn_kwargs() -> dict:
    """worker 进程隔离参数(复用 supervisor 的平台隔离 helper):Win 独立进程组 /
    POSIX 新会话,使 parent 能显式转发信号(否则 Ctrl-C 直接打到 worker、绕过 parent
    编排)。stdio 继承,worker 的 setup_logging 自带控制台+文件 handler,日志直通 parent 控制台。"""
    return {"stdout": None, "stderr": None, "stdin": None, **process_group_kwargs()}


def _forwardable_signals() -> list:
    """parent 要转发给 worker 的信号。Windows 仅 SIGINT(Ctrl-C;无 SIGTERM);POSIX 两者。"""
    if os.name == "nt":
        return [signal.SIGINT]
    return [signal.SIGINT, signal.SIGTERM]


def _send_shutdown(proc) -> None:
    """向 worker 进程组发优雅关闭信号。Win:CTRL_BREAK_EVENT(需 worker 在独立进程组);
    POSIX:killpg(SIGTERM)。进程已不在 → 静默。"""
    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass


def _force_kill(proc) -> None:
    """超时兜底:worker 仍运行 → 强杀;已退出 → no-op。"""
    if proc.poll() is None:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001, S110
            pass


def main() -> None:
    """入口分派:`--worker` → 跑应用(worker);否则 → parent 监督器。"""
    if _WORKER_FLAG in sys.argv[1:]:
        _run_worker()
    else:
        _run_parent()


def _run_worker() -> None:
    """worker:实际应用(create_app + server.run)。退出码 81=请求重启,0=正常;
    parent 监督器在其退出码上决定拉新 / 退出。"""
    import uvicorn

    from llm_manager.app import create_app

    app = create_app(legacy_yaml=Path("config.yaml"))
    cfg = app.state.config_store.snapshot()
    server = uvicorn.Server(
        uvicorn.Config(app, host=cfg.program.host, port=cfg.program.port, lifespan="on")
    )
    app.state.uvicorn_server = server
    server.run()
    sys.exit(exit_code_for(getattr(app.state, "restart_requested", False)))


def _run_parent() -> None:
    """parent 监督器:常驻,不碰 DB / 不持 app 状态。spawn worker、转发 Ctrl-C/SIGTERM、
    按 worker 退出码决定拉新(81)/ 退出(0 或崩溃)。严格顺序:等 rc 到手才 spawn 下一个,
    故无双 worker 并存、无端口竞争。崩溃不自愈(可见失败)。"""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
    )
    while True:
        proc = _spawn_worker()
        _forward_signals(proc)
        rc = proc.wait()
        if _should_respawn(rc) and not _shutting_down:
            logger.info("worker 请求重启(exit %s),拉起新 worker...", rc)
            continue
        logger.info("worker 退出(码 %s),parent 退出。", rc)
        sys.exit(rc if isinstance(rc, int) else 0)


def _spawn_worker():
    """spawn 一个 worker(继承 stdio,日志直通 parent 控制台)。"""
    return subprocess.Popen(_worker_command(), **_spawn_kwargs())


_shutting_down = False  # 信号转发置位;防止重启间隙收到的信号误触发新 worker 关闭


def _forward_signals(proc) -> None:
    """安装信号转发:parent 收 Ctrl-C/SIGTERM → 转发 worker 进程组使其优雅关闭;
    并起超时定时器,_SHUTDOWN_GRACE 秒后仍存活 → 强杀(防 worker 卡死拽死 parent)。
    每轮 worker 重装(指向当轮 proc);_shutting_down 复位。"""
    global _shutting_down
    _shutting_down = False

    def _on_signal(signum, frame):
        global _shutting_down
        if _shutting_down:
            return
        _shutting_down = True
        logger.info("收到信号 %s,转发给 worker 优雅关闭...", signum)
        _send_shutdown(proc)
        watchdog = threading.Timer(_SHUTDOWN_GRACE, _force_kill, args=(proc,))
        watchdog.daemon = True
        watchdog.start()

    for sig in _forwardable_signals():
        signal.signal(sig, _on_signal)
