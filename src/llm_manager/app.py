"""组合根:setup_logging + 加载/校验配置 + 带 lifespan 的 FastAPI app。

lifespan 打开 DB、DeviceMonitor(初始刷新)与 httpx 客户端池;关闭时逐一收口。
parent/worker 监督器在 runner.py
(``python -m llm_manager`` 入口)。"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from llm_manager import config
from llm_manager import tray as tray_host
from llm_manager.data import logs as _logs
from llm_manager.data.log_handler import SystemLogHandler, setup_logging
from llm_manager.data.persistence import open_db
from llm_manager.devices import DeviceMonitor, build_adapters
from llm_manager.gateway.api.models import build_models_response
from llm_manager.gateway.routes import register_routes
from llm_manager.realtime import DeviceFeed, ModelFeed
from llm_manager.runtime import background
from llm_manager.runtime.lifecycle import Lifecycle
from llm_manager.runtime.probes import probe_registry
from llm_manager.runtime.update import UpdateStatus, check_update
from llm_manager.supervisor import Supervisor

logger = logging.getLogger(__name__)


async def _startup_update_check(app: FastAPI) -> None:
    """程序启动时后台检测一次自更新(git fetch,网络):结果缓存到
    app.state.update_status,前端只读缓存。仅此一次,此后无任何自动检测——
    手动检查走 POST /api/update/check。to_thread 外包阻塞的 git 调用。
    generation 守卫:若期间用户已手动 check(gen 递增),启动的慢结果不得覆盖
    更新的手动结果(否则启动 fetch 超时窗口内手动结果会被回退成启动态)。"""
    try:
        result = await asyncio.to_thread(check_update)
        if app.state.update_check_generation == 0:
            app.state.update_status = result
    except Exception:
        logger.exception("startup update check failed")
        if app.state.update_check_generation == 0:
            app.state.update_status = UpdateStatus(ok=False, error="启动更新检查失败")


def create_app(db_path: Path | None = None) -> FastAPI:
    resolved_db = Path(db_path or os.environ.get("LLM_MANAGER_DB_PATH", "data/llm_manager.db"))
    db = open_db(resolved_db)
    _logs.init(db)  # 接线日志存储(幂等)
    try:
        from llm_manager.data.config_store import ConfigStore, initialize

        initialize(db)
        store = ConfigStore(db)
        cfg = store.snapshot()
        errors = config.validate(cfg)
        if errors:
            raise ValueError("Invalid config:\n" + "\n".join(f"  - {e}" for e in errors))
    except Exception:
        db.conn.close()
        raise
    setup_logging(level=cfg.program.log_level)  # log_level 接线
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
            background.log_retention_loop(
                db, lambda: background.retention_from_store(store), log_stop
            )
        )
        heartbeat_task = asyncio.create_task(background.heartbeat_loop(db, log_stop))
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
        # 启动自更新检测(后台一次;结果缓存 app.state.update_status,前端只读)
        update_task = asyncio.create_task(_startup_update_check(app))
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
                if not update_task.done():
                    update_task.cancel()
                if not idle_task.done():
                    idle_task.cancel()
                if not auto_task.done():
                    auto_task.cancel()
                await asyncio.gather(update_task, idle_task, auto_task, return_exceptions=True)
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
    app.state.update_status = None  # 自更新启动检测结果缓存(后台任务填充;None=检测中)
    app.state.update_check_generation = 0  # 手动 check 递增;启动任务只在仍为 0 时写缓存
    return app


def create_dev_app() -> FastAPI:
    """``uvicorn --factory --reload`` 的无参工厂(开发模式)。"""
    app = create_app()
    return app
