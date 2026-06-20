"""Composition root: setup_logging + load/validate config + FastAPI app.

Plan 1 wires only /health. Plan 2 adds a `lifespan` context (DB, httpx pool,
DeviceMonitor), /v1/models, OPTIONS preflight, and the proxy stub."""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from fastapi import FastAPI

from llm_manager import config
from llm_manager.gateway.routes import register_routes

_logging_configured = False


def setup_logging(level: str = "INFO", log_dir: str = "logs") -> None:
    """Configure root logger once: stdout console + TimedRotatingFileHandler.
    Idempotent (clears existing handlers). No custom manager class."""
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
    app = FastAPI(title="LLM-Manager")
    register_routes(app)
    # Plan 2 adds a `lifespan` context here to open/close DB, httpx pool, DeviceMonitor.
    return app


def main() -> None:
    import uvicorn

    cfg_path = Path("config.yaml")
    app = create_app(cfg_path)
    cfg = config.load(cfg_path)
    uvicorn.run(app, host=cfg.program.host, port=cfg.program.port)
