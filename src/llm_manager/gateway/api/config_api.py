"""System config write-back API: GET/PUT /api/config*, /api/system/info, restart 检测.

校验层 = Pydantic 请求模型(FastAPI 自动 422)。写经 set_settings(多键原子)→ store.reload()。
restart 检测:对比 snapshot.program 的 host/port/db_path/log_dir 与 app.state.boot_program(启动期捕获)。
读穿仅 system_settings 影响的消费方(idle 循环/tray/logging)——lifecycle/catalog/models 随 P2 模型 CRUD。
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Request

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
