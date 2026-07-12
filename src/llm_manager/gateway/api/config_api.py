"""System config write-back API: GET/PUT /api/config*, /api/system/info, restart 检测.

校验层 = Pydantic 请求模型(FastAPI 自动 422)。写经 set_settings(多键原子)→ store.reload()。
restart 检测:对比 snapshot.program 的 host/port/db_path/log_dir 与 app.state.boot_program(启动期捕获)。
读穿仅 system_settings 影响的消费方(idle 循环/tray/logging)——lifecycle/catalog/models 随 P2 模型 CRUD。
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Request

from llm_manager.data.config_store import get_setting

try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version
    try:
        _VERSION = _pkg_version("llm-manager")
    except PackageNotFoundError:
        _VERSION = "unknown"
except Exception:
    _VERSION = "unknown"

_RESTART_FIELDS = ("host", "port", "db_path", "log_dir")


def _store(request: Request):
    return request.app.state.config_store


def _db(request: Request):
    return request.app.state.db


def _boot(request: Request) -> dict:
    return request.app.state.boot_program


def _restart_fields(snapshot, boot: dict) -> list[str]:
    return [f for f in _RESTART_FIELDS if str(getattr(snapshot.program, f)) != str(boot.get(f))]


def register_config_routes(api: APIRouter) -> None:

    @api.get("/system/info")
    def system_info(request: Request) -> dict:
        cfg = _store(request).snapshot()
        boot = _boot(request)
        started_at = getattr(request.app.state, "started_at", None) or time.time()
        db_path = Path(boot.get("db_path", cfg.program.db_path))
        return {
            "version": _VERSION,
            "started_at": started_at,
            "uptime_s": max(0.0, time.time() - started_at),
            "db_path": str(db_path),
            "db_size_bytes": db_path.stat().st_size if db_path.exists() else None,
            "log_dir": boot.get("log_dir", cfg.program.log_dir),
        }

    @api.get("/config")
    def get_config(request: Request) -> dict:
        cfg = _store(request).snapshot()
        boot = _boot(request)
        p = cfg.program
        return {
            "program": {
                "host": p.host, "port": p.port, "alive_time": p.alive_time,
                "log_level": p.log_level, "log_dir": p.log_dir, "db_path": p.db_path,
                "claude_settings_path": p.claude_settings_path,
            },
            "wol": ({"broadcast_address": cfg.wol.broadcast_address,
                     "mac_address": cfg.wol.mac_address} if cfg.wol is not None else None),
            "claude": cfg.claude_configs,
            "logs": {
                "time_enabled": get_setting(_db(request), "log_retention_time_enabled") == "1",
                "days": int(get_setting(_db(request), "log_retention_days") or 30),
                "count_enabled": get_setting(_db(request), "log_retention_count_enabled") == "1",
                "count": int(get_setting(_db(request), "log_retention_count") or 10),
            },
            "restart_fields": _restart_fields(cfg, boot),
        }
