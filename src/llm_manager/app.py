"""Composition root: setup_logging + load/validate config + FastAPI app with lifespan.

lifespan opens the DB, DeviceMonitor (initial refresh), and an httpx-client pool;
closes them on shutdown. Plan 3 fills the proxy + lifecycle wiring."""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from llm_manager import config
from llm_manager.data import logs as _logs
from llm_manager.data.log_handler import SystemLogHandler
from llm_manager.data.persistence import log_close_open_system_sessions, open_db
from llm_manager.devices import ENUMERATORS, DeviceMonitor
from llm_manager.gateway.api.models import build_models_response
from llm_manager.gateway.routes import register_routes
from llm_manager.probes import probe_registry
from llm_manager.realtime import DeviceFeed, ModelFeed
from llm_manager.runtime.lifecycle import Lifecycle
from llm_manager.runtime import background
from llm_manager.runtime.log_retention import log_retention_loop, retention_settings
from llm_manager.supervisor import Supervisor
from llm_manager.tray import host as tray_host

logger = logging.getLogger(__name__)


def _cleanup_old_logs(log_dir: str, keep: int = 10) -> None:
    """保留最近 keep 个 llm-manager_*.log(按 mtime),删旧的。"""
    files = sorted(Path(log_dir).glob("llm-manager_*.log"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in files[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass


def setup_logging(level: str = "INFO", log_dir: str = "logs") -> None:
    """配置 root logger(可重配):控制台 + 每次启动一个时间戳文件(留 10 个)。
    每次启动 = 新文件 logs/llm-manager_{ts}.log(非按天轮换,避免长期堆一个文件)。"""
    numeric = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(numeric)
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(numeric)
    console.setFormatter(fmt)
    root.addHandler(console)
    try:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        log_file = Path(log_dir) / f"llm-manager_{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(numeric)
        fh.setFormatter(fmt)
        root.addHandler(fh)
        _cleanup_old_logs(log_dir, keep=10)
        logger.info("logging to %s", log_file)
    except OSError:
        pass
    logging.getLogger("httpx").setLevel(logging.WARNING)  # 降噪:每请求一行太吵,REQ/RESP 已覆盖


def create_app(db_path: Path | None = None, *, legacy_yaml: Path | None = None) -> FastAPI:
    setup_logging()
    resolved_db = Path(db_path or os.environ.get("LLM_MANAGER_DB_PATH", "data/llm_manager.db"))
    db = open_db(resolved_db)
    _logs.init(db)   # 接线日志存储(幂等)
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
    logger.info("config loaded (DB %s): %d models, %s:%d, alive %dmin",
                resolved_db, len(cfg.models), cfg.program.host, cfg.program.port, cfg.program.alive_time)
    monitor = DeviceMonitor(ENUMERATORS, config.referenced_devices(cfg))
    supervisor = Supervisor()
    lifecycle = Lifecycle(get_cfg=store.snapshot, supervisor=supervisor, devices=monitor,
                          probes=probe_registry, db=db)
    clients: dict[int, httpx.AsyncClient] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.db = db
        app.state.monitor = monitor
        app.state.clients = clients
        app.state.lifecycle = lifecycle
        app.state.cfg = cfg
        app.state.loop = asyncio.get_running_loop()
        # === 系统日志会话:handler 任意线程 emit → capture_system → flush_loop 落库 ===
        # 崩溃/强杀残留的上次 system 会话(end_time IS NULL)先统一收口(D6),再开新会话;
        # 收口刻意放在 app 接线而非 logs.start_system_session:保持 logs 模块测试隔离。
        n_residual = log_close_open_system_sessions(db)
        if n_residual:
            logger.info("closed %d crash-residual system log session(s)", n_residual)
        _logs.start_system_session()
        sys_handler = SystemLogHandler(_logs.capture_system)
        logging.getLogger().addHandler(sys_handler)
        log_stop = asyncio.Event()
        flush_task = asyncio.create_task(_logs.flush_loop(log_stop))
        retention_task = asyncio.create_task(
            log_retention_loop(db, lambda: retention_settings(db), log_stop))
        await asyncio.to_thread(monitor.refresh)
        online = sorted(monitor.online_devices())
        logger.info("devices online: %s", ", ".join(online) if online else "(none)")
        app.state.device_feed = DeviceFeed(monitor)  # 概览设备栏 SSE 源(订阅门控 2s 刷新)
        app.state.model_feed = ModelFeed(lambda: build_models_response(store.snapshot()))  # 模型 SSE 源(读穿:变更检测推送)
        stop_event = asyncio.Event()
        auto_models = [n for n, m in cfg.models.items() if m.auto_start]
        auto_task = asyncio.create_task(
            background.auto_start(lifecycle, auto_models, cfg, monitor,
                                  timeout=lifecycle.startup_timeout + background.AUTO_START_MARGIN,
                                  stop_event=stop_event))
        idle_task = asyncio.create_task(
            background.idle_reclamation_loop(lifecycle, store.snapshot, stop_event))
        # 系统托盘(守卫:pystray 可用 + 需 uvicorn server 句柄做优雅退出 + claude_settings_path)
        tray = None
        server = getattr(app.state, "uvicorn_server", None)

        def _request_restart() -> None:
            # tray 线程调用:置 flag(主线程 main() 末尾读)+ 线程安全翻 should_exit(立即,
            # 无需延迟——tray 是本地动作,无 HTTP 响应要冲刷)。
            app.state.restart_requested = True
            app.state.loop.call_soon_threadsafe(setattr, server, "should_exit", True)

        if (tray_host.is_tray_available() and server is not None
                and cfg.program.claude_settings_path):
            tray = tray_host.SystemTray(
                lifecycle=lifecycle, get_cfg=store.snapshot, monitor=monitor,
                loop=app.state.loop, server=server,
                settings_path=cfg.program.claude_settings_path,
                startup_timeout=lifecycle.startup_timeout,
                auto_start_margin=background.AUTO_START_MARGIN,
                request_restart=_request_restart,
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
                await asyncio.gather(flush_task, retention_task, return_exceptions=True)
                await _logs.flush()
            finally:
                logging.getLogger().removeHandler(sys_handler)
                _logs.end_system_session()
            for client in clients.values():
                await client.aclose()
            db.conn.close()

    app = FastAPI(title="LLM-Manager", lifespan=lifespan)
    register_routes(app, lifecycle, db, clients)
    app.state.cfg = cfg
    app.state.config_store = store
    app.state.resolved_db = str(resolved_db)   # 供 system_info 算 db_size_bytes(不暴露路径键)
    app.state.boot_program = {f: str(getattr(cfg.program, f)) for f in ("host", "port", "claude_settings_path", "log_level")}
    app.state.started_at = time.time()
    return app


def create_dev_app() -> FastAPI:
    """No-arg factory for ``uvicorn --factory --reload`` (development mode)."""
    import types
    app = create_app(legacy_yaml=Path("config.yaml"))
    app.state.uvicorn_server = types.SimpleNamespace(should_exit=False)
    return app


RESTART_EXIT_CODE = 81


def exit_code_for(restart_requested: bool) -> int:
    """main() 退出码:restart_requested → 哨兵码(监督器在其上重启),否则 0(正常退出)。"""
    return RESTART_EXIT_CODE if restart_requested else 0


def main() -> None:
    import uvicorn
    app = create_app(legacy_yaml=Path("config.yaml"))
    cfg = app.state.cfg
    server = uvicorn.Server(
        uvicorn.Config(app, host=cfg.program.host, port=cfg.program.port, lifespan="on"))
    app.state.uvicorn_server = server
    server.run()
    sys.exit(exit_code_for(getattr(app.state, "restart_requested", False)))
