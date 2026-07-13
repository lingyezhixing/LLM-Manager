"""System config write-back API: GET/PUT /api/config*, /api/system/info, restart 检测.

校验层 = Pydantic 请求模型(FastAPI 自动 422)。写经 set_settings(多键原子)→ store.reload()。
restart 检测:对比 snapshot.program 的 host/port/db_path/log_dir 与 app.state.boot_program(启动期捕获)。
读穿仅 system_settings 影响的消费方(idle 循环/tray/logging)——lifecycle/catalog/models 随 P2 模型 CRUD。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from llm_manager.data.config_store import get_setting, set_settings

try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version
    try:
        _VERSION = _pkg_version("llm-manager")
    except PackageNotFoundError:
        _VERSION = "unknown"
except Exception:
    _VERSION = "unknown"

_RESTART_FIELDS = ("host", "port", "db_path", "log_dir")


class ProgramUpdate(BaseModel):
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    alive_time: int | None = Field(default=None, ge=0)
    log_level: str | None = None
    log_dir: str | None = None
    db_path: str | None = None
    claude_settings_path: str | None = None


class WolUpdate(BaseModel):
    broadcast_address: str
    mac_address: str

class ClaudeConfigsUpdate(BaseModel):
    configs: dict[str, dict[str, str]]

class LogRetentionUpdate(BaseModel):
    time_enabled: bool | None = None
    days: int | None = Field(default=None, ge=1)
    count_enabled: bool | None = None
    count: int | None = Field(default=None, ge=1)


def _store(request: Request):
    return request.app.state.config_store


def _db(request: Request):
    return request.app.state.db


def _boot(request: Request) -> dict:
    return request.app.state.boot_program


def _restart_fields(snapshot, boot: dict) -> list[str]:
    return [f for f in _RESTART_FIELDS if str(getattr(snapshot.program, f)) != str(boot.get(f))]


def _serving() -> list[str]:
    """当前正在服务(ROUTING 且 pending>0)的模型——restart 会中断它们。"""
    from llm_manager import state
    return [n for n in state.routing_names() if state.pending_count(n) > 0]


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

    @api.put("/config/program")
    def put_program(request: Request, body: ProgramUpdate) -> dict:
        updates: dict[str, str] = {}
        for f in ("host", "log_level", "log_dir", "db_path", "claude_settings_path"):
            v = getattr(body, f)
            if v is not None:
                updates[f] = v
        for f in ("port", "alive_time"):
            v = getattr(body, f)
            if v is not None:
                updates[f] = str(v)
        if updates:
            set_settings(_db(request), updates)
        cfg = _store(request).reload()
        rf = _restart_fields(cfg, _boot(request))
        return {"needs_restart": bool(rf), "restart_fields": rf, "serving": _serving()}

    @api.put("/config/wol")
    def put_wol(request: Request, body: WolUpdate) -> dict:
        updates: dict[str, str] = {}
        if body.broadcast_address is not None:
            updates["wol_broadcast"] = body.broadcast_address
        if body.mac_address is not None:
            updates["wol_mac"] = body.mac_address
        if updates:
            set_settings(_db(request), updates)
        cfg = _store(request).reload()
        rf = _restart_fields(cfg, _boot(request))
        return {"needs_restart": bool(rf), "restart_fields": rf, "serving": _serving()}

    @api.put("/config/claude")
    def put_claude(request: Request, body: ClaudeConfigsUpdate) -> dict:
        set_settings(_db(request), {"claude_configs": json.dumps(body.configs, ensure_ascii=False)})
        cfg = _store(request).reload()
        rf = _restart_fields(cfg, _boot(request))
        return {"needs_restart": bool(rf), "restart_fields": rf, "serving": _serving()}

    @api.put("/config/logs")
    def put_logs(request: Request, body: LogRetentionUpdate) -> dict:
        updates: dict[str, str] = {}
        if body.time_enabled is not None:
            updates["log_retention_time_enabled"] = "1" if body.time_enabled else "0"
        if body.days is not None:
            updates["log_retention_days"] = str(body.days)
        if body.count_enabled is not None:
            updates["log_retention_count_enabled"] = "1" if body.count_enabled else "0"
        if body.count is not None:
            updates["log_retention_count"] = str(body.count)
        if updates:
            set_settings(_db(request), updates)
        _store(request).reload()                  # 日志规则不进 AppConfig 快照,但 reload 保持新鲜
        return {"needs_restart": False, "restart_fields": [], "serving": _serving()}

    @api.post("/config/reload")
    def reload_config(request: Request) -> dict:
        cfg = _store(request).reload()
        # 热字段:log_level 即时重配 logging
        from llm_manager.app import setup_logging
        setup_logging(cfg.program.log_level, cfg.program.log_dir)
        rf = _restart_fields(cfg, _boot(request))
        return {"needs_restart": bool(rf), "restart_fields": rf, "serving": _serving()}

    @api.get("/config/restart-status")
    def restart_status(request: Request) -> dict:
        cfg = _store(request).snapshot()
        rf = _restart_fields(cfg, _boot(request))
        return {"needs_restart": bool(rf), "restart_fields": rf, "serving": _serving()}
