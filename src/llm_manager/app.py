"""Composition root: setup_logging + load/validate config + FastAPI app with lifespan.

lifespan opens the DB, DeviceMonitor (initial refresh), and an httpx-client pool;
closes them on shutdown. Plan 3 fills the proxy + lifecycle wiring."""
from __future__ import annotations

import asyncio
import datetime
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from llm_manager import config
from llm_manager.data.persistence import open_db
from llm_manager.devices import ENUMERATORS, DeviceMonitor
from llm_manager.gateway.api.models import build_models_response
from llm_manager.gateway.routes import register_routes
from llm_manager.probes import probe_registry
from llm_manager.realtime import DeviceFeed, ModelFeed
from llm_manager.runtime.lifecycle import Lifecycle
from llm_manager.runtime import background
from llm_manager.supervisor import Supervisor
from llm_manager.tray import host as tray_host

_logging_configured = False
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
    """配置 root logger(一次性):控制台 + 每次启动一个时间戳文件(留 10 个)。
    每次启动 = 新文件 logs/llm-manager_{ts}.log(非按天轮换,避免长期堆一个文件)。"""
    global _logging_configured
    if _logging_configured:
        return
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
    _logging_configured = True


def create_app(config_path: Path) -> FastAPI:
    setup_logging()
    cfg = config.load(config_path)
    errors = config.validate(cfg)
    if errors:
        raise ValueError("Invalid config:\n" + "\n".join(f"  - {e}" for e in errors))
    logger.info("config loaded: %d models, %s:%d, alive %dmin",
                len(cfg.models), cfg.program.host, cfg.program.port, cfg.program.alive_time)
    db = open_db(Path(cfg.program.db_path))
    monitor = DeviceMonitor(ENUMERATORS, config.referenced_devices(cfg))
    supervisor = Supervisor()
    lifecycle = Lifecycle(cfg=cfg, supervisor=supervisor, devices=monitor, probes=probe_registry)
    clients: dict[int, httpx.AsyncClient] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.db = db
        app.state.monitor = monitor
        app.state.clients = clients
        app.state.lifecycle = lifecycle
        app.state.cfg = cfg
        app.state.loop = asyncio.get_running_loop()
        await asyncio.to_thread(monitor.refresh)
        online = sorted(monitor.online_devices())
        logger.info("devices online: %s", ", ".join(online) if online else "(none)")
        app.state.device_feed = DeviceFeed(monitor)  # 概览设备栏 SSE 源(订阅门控 2s 刷新)
        app.state.model_feed = ModelFeed(lambda: build_models_response(cfg))  # 模型 SSE 源(变更检测推送)
        stop_event = asyncio.Event()
        auto_models = [n for n, m in cfg.models.items() if m.auto_start]
        alive_sec = cfg.program.alive_time * 60.0
        auto_task = asyncio.create_task(
            background.auto_start(lifecycle, auto_models, cfg, monitor,
                                  timeout=lifecycle.startup_timeout + background.AUTO_START_MARGIN,
                                  stop_event=stop_event))
        idle_task = asyncio.create_task(
            background.idle_reclamation_loop(lifecycle, alive_sec, stop_event))
        # 系统托盘(守卫:pystray 可用 + 需 uvicorn server 句柄做优雅退出 + claude_settings_path)
        tray = None
        server = getattr(app.state, "uvicorn_server", None)
        if (tray_host.is_tray_available() and server is not None
                and cfg.program.claude_settings_path):
            tray = tray_host.SystemTray(
                lifecycle=lifecycle, cfg=cfg, monitor=monitor,
                loop=app.state.loop, server=server,
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
            for client in clients.values():
                await client.aclose()
            db.conn.close()

    app = FastAPI(title="LLM-Manager", lifespan=lifespan)
    register_routes(app, lifecycle, cfg, db, clients)
    return app


def main() -> None:
    import uvicorn

    cfg_path = Path("config.yaml")
    app = create_app(cfg_path)
    cfg = config.load(cfg_path)
    # 编程式 uvicorn:持 Server 句柄供系统托盘「退出」优雅关停
    # (server.should_exit=True → uvicorn 关停 → lifespan finally:unload_all + 关 clients/db)
    server = uvicorn.Server(
        uvicorn.Config(app, host=cfg.program.host, port=cfg.program.port, lifespan="on"))
    app.state.uvicorn_server = server
    server.run()
