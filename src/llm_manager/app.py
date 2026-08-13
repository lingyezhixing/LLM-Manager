"""Composition root: setup_logging + load/validate config + FastAPI app with lifespan.

lifespan opens the DB, DeviceMonitor (initial refresh), and an httpx-client pool;
closes them on shutdown. Plan 3 fills the proxy + lifecycle wiring."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from llm_manager import config, supervisor
from llm_manager import tray as tray_host
from llm_manager.data import logs as _logs
from llm_manager.data.log_handler import SystemLogHandler, setup_logging
from llm_manager.data.persistence import open_db
from llm_manager.devices import DeviceMonitor, build_adapters
from llm_manager.gateway.api.config_api import RESTART_EXIT_CODE
from llm_manager.gateway.api.models import build_models_response
from llm_manager.gateway.routes import register_routes
from llm_manager.probes import probe_registry
from llm_manager.realtime import DeviceFeed, ModelFeed
from llm_manager.runtime import background
from llm_manager.runtime.heartbeat import heartbeat_loop
from llm_manager.runtime.lifecycle import Lifecycle
from llm_manager.runtime.log_retention import log_retention_loop, retention_from_store
from llm_manager.supervisor import Supervisor

logger = logging.getLogger(__name__)


def create_app(db_path: Path | None = None, *, legacy_yaml: Path | None = None) -> FastAPI:
    resolved_db = Path(db_path or os.environ.get("LLM_MANAGER_DB_PATH", "data/llm_manager.db"))
    db = open_db(resolved_db)
    _logs.init(db)  # 接线日志存储(幂等)
    try:
        from llm_manager.data.config_store import ConfigStore, initialize

        initialize(db, legacy_yaml)
        store = ConfigStore(db)
        cfg = store.snapshot()
        errors = config.validate(cfg)
        if errors:
            raise ValueError("Invalid config:\n" + "\n".join(f"  - {e}" for e in errors))
    except Exception:
        db.conn.close()
        raise
    setup_logging(level=cfg.program.log_level)  # log_level 接线(此前硬编码 INFO,该参数从未生效)
    logger.info(
        "config loaded (DB %s): %d models, %s:%d, alive %dmin",
        resolved_db,
        len(cfg.models),
        cfg.program.host,
        cfg.program.port,
        cfg.program.alive_time,
    )
    # referenced 动态化:配置运行时可变(WebUI 在线加模型),按活配置重算设备引用,
    # 否则新模型引用的设备名不进 online → 启动报 no adaptive scheme(需重启才生效)
    monitor = DeviceMonitor(build_adapters(), lambda: config.referenced_devices(store.snapshot()))
    supervisor = Supervisor()
    lifecycle = Lifecycle(
        get_cfg=store.snapshot, supervisor=supervisor, devices=monitor, probes=probe_registry, db=db
    )
    clients: dict[int, httpx.AsyncClient] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.db = db
        app.state.monitor = monitor
        app.state.clients = clients
        app.state.lifecycle = lifecycle
        app.state.loop = asyncio.get_running_loop()
        # === 系统日志会话:handler 任意线程 emit → capture_system → flush_loop 落库 ===
        # 无需启动收口:心跳(heartbeat_loop)已把上次运行中会话/运行段的 end_time 维持到
        # ≈死亡时刻;新进程 live_session_ids/live_segment_ids 为空 → 残留天然 status=ended。
        _logs.start_system_session()
        sys_handler = SystemLogHandler(_logs.capture_system)
        logging.getLogger().addHandler(sys_handler)
        log_stop = asyncio.Event()
        flush_task = asyncio.create_task(_logs.flush_loop(log_stop))
        retention_task = asyncio.create_task(
            log_retention_loop(db, lambda: retention_from_store(store), log_stop)
        )
        heartbeat_task = asyncio.create_task(heartbeat_loop(db, log_stop))
        await asyncio.to_thread(monitor.refresh)
        online = sorted(monitor.online_devices())
        logger.info("devices online: %s", ", ".join(online) if online else "(none)")
        app.state.device_feed = DeviceFeed(monitor)  # 概览设备栏 SSE 源(订阅门控 2s 刷新)
        app.state.model_feed = ModelFeed(
            lambda: build_models_response(store.snapshot())
        )  # 模型 SSE 源(读穿:变更检测推送)
        stop_event = asyncio.Event()
        auto_models = config.auto_start_models(cfg)
        auto_task = asyncio.create_task(
            background.auto_start(
                lifecycle,
                auto_models,
                cfg,
                monitor,
                timeout=lifecycle.startup_timeout + background.AUTO_START_MARGIN,
                stop_event=stop_event,
            )
        )
        idle_task = asyncio.create_task(
            background.idle_reclamation_loop(lifecycle, store.snapshot, stop_event)
        )
        # 系统托盘(守卫:pystray 可用 + 需 uvicorn server 句柄做优雅退出;claude_settings_path 可空,
        # 未配置时托盘照常启动,仅 Claude 预设子菜单隐藏——首次启动不该缺失托盘)
        tray = None
        server = getattr(app.state, "uvicorn_server", None)

        if tray_host.is_tray_available() and server is not None:
            tray = tray_host.SystemTray(
                lifecycle=lifecycle,
                get_cfg=store.snapshot,
                monitor=monitor,
                loop=app.state.loop,
                server=server,
                settings_path=cfg.program.claude_settings_path,
                startup_timeout=lifecycle.startup_timeout,
                auto_start_margin=background.AUTO_START_MARGIN,
            )
            tray.start()
            app.state.tray = tray
        try:
            yield
        finally:
            if tray is not None:
                tray.shutdown()
            stop_event.set()
            try:
                await lifecycle.unload_all()
            finally:
                if not idle_task.done():
                    idle_task.cancel()
                if not auto_task.done():
                    auto_task.cancel()
                await asyncio.gather(idle_task, auto_task, return_exceptions=True)
            # === 系统日志收尾:停 flush_loop → 兜底清空剩余 pending → 摘 handler → 收口会话 ===
            try:
                log_stop.set()
                await asyncio.gather(
                    flush_task, retention_task, heartbeat_task, return_exceptions=True
                )
                await _logs.flush()
            finally:
                logging.getLogger().removeHandler(sys_handler)
                _logs.end_system_session()
            for client in clients.values():
                await client.aclose()
            db.conn.close()

    app = FastAPI(title="LLM-Manager", lifespan=lifespan)
    register_routes(app, lifecycle, db, clients)
    app.state.config_store = store
    app.state.resolved_db = str(resolved_db)  # 供 system_info 算 db_size_bytes(不暴露路径键)
    app.state.boot_program = {
        f: str(getattr(cfg.program, f))
        for f in ("host", "port", "claude_settings_path", "log_level")
    }
    app.state.started_at = time.time()
    return app


def create_dev_app() -> FastAPI:
    """No-arg factory for ``uvicorn --factory --reload`` (development mode)."""
    app = create_app(legacy_yaml=Path("config.yaml"))
    return app


def exit_code_for(restart_requested: bool) -> int:
    """worker 退出码:restart_requested → 哨兵码(parent 监督器在其上拉新 worker),否则 0(正常退出)。"""
    return RESTART_EXIT_CODE if restart_requested else 0


# ==================== parent 监督器 ====================
# 配置变更重启 = 程序内置的 parent+worker(类 NapCat):parent 常驻、不碰 DB,只 spawn
# worker / 转发信号 / 按退出码拉新。worker 每次都是全新进程 → OS 回收一切,构造性干净
# (无进程内重启的隐藏状态泄漏)。退出码协议:81=请求重启,0=正常,其他=崩溃(不自愈)。

_WORKER_FLAG = "--worker"
_SHUTDOWN_GRACE = 10.0  # worker 优雅关闭超时(秒);超时强杀,防卡死拽死 parent


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
    return {"stdout": None, "stderr": None, "stdin": None, **supervisor.process_group_kwargs()}


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
