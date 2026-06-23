"""Composition root: setup_logging + load/validate config + FastAPI app with lifespan.

lifespan opens the DB, DeviceMonitor (initial refresh), and an httpx-client pool;
closes them on shutdown. Plan 3 fills the proxy + lifecycle wiring."""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from llm_manager import config
from llm_manager.data.persistence import open_db
from llm_manager.devices import (
    DEVICES, DeviceMonitor, detect_amd_apu, is_lhm_available, lhm_sensors_780m,
)
from llm_manager.gateway.routes import register_routes
from llm_manager.probes import probe_registry
from llm_manager.runtime.lifecycle import Lifecycle
from llm_manager.runtime import background
from llm_manager.supervisor import Supervisor

_logging_configured = False


def setup_logging(level: str = "INFO", log_dir: str = "logs") -> None:
    """Configure root logger once: stdout console + TimedRotatingFileHandler. Idempotent."""
    global _logging_configured
    if _logging_configured:
        return
    numeric = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(numeric)
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s %(message)s", "%H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(numeric)
    console.setFormatter(fmt)
    root.addHandler(console)
    try:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.TimedRotatingFileHandler(
            Path(log_dir) / "llm-manager.log", when="midnight", backupCount=10, encoding="utf-8"
        )
        fh.setLevel(numeric)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass
    _logging_configured = True


def create_app(config_path: Path) -> FastAPI:
    setup_logging()
    cfg = config.load(config_path)
    errors = config.validate(cfg)
    if errors:
        raise ValueError("Invalid config:\n" + "\n".join(f"  - {e}" for e in errors))
    db = open_db(Path(cfg.program.db_path))
    devices = dict(DEVICES)  # 拷贝,不污染模块级常量
    if is_lhm_available():
        devices["780m"] = lambda: detect_amd_apu("780m", lhm_sensors_780m)
    monitor = DeviceMonitor(devices)
    supervisor = Supervisor()
    lifecycle = Lifecycle(cfg=cfg, supervisor=supervisor, devices=monitor, probes=probe_registry)
    clients: dict[int, httpx.AsyncClient] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.db = db
        app.state.monitor = monitor
        app.state.clients = clients
        await asyncio.to_thread(monitor.refresh)
        stop_event = asyncio.Event()
        auto_models = [n for n, m in cfg.models.items() if m.auto_start]
        alive_sec = cfg.program.alive_time * 60.0
        auto_task = asyncio.create_task(
            background.auto_start(lifecycle, auto_models, cfg, monitor,
                                  timeout=lifecycle.startup_timeout + background.AUTO_START_MARGIN,
                                  stop_event=stop_event))
        idle_task = asyncio.create_task(
            background.idle_reclamation_loop(lifecycle, alive_sec, stop_event))
        try:
            yield
        finally:
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
    uvicorn.run(app, host=cfg.program.host, port=cfg.program.port)
