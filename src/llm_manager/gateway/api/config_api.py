"""System config write-back API: GET/PUT /api/config*, /api/system/info, restart 检测.

校验层 = Pydantic 请求模型(FastAPI 自动 422)。写经 set_settings(多键原子)→ store.reload()。
restart 检测:对比 snapshot.program 的 host/port/db_path/log_dir 与 app.state.boot_program(启动期捕获)。
读穿仅 system_settings 影响的消费方(idle 循环/tray/logging)——lifecycle/catalog/models 随 P2 模型 CRUD。
"""
from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from llm_manager.config import AppConfig, Command, ModelConfig, Scheme, _norm_device
from llm_manager.data.config_store import (
    ConfigValidationFailed,
    ModelExists,
    ModelNotFound,
    get_setting,
    mutate_appconfig,
    set_settings,
)

try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version
    try:
        _VERSION = _pkg_version("llm-manager")
    except PackageNotFoundError:
        _VERSION = "unknown"
except Exception:
    _VERSION = "unknown"

_RESTART_FIELDS = ("host", "port", "db_path", "log_dir", "claude_settings_path", "log_level")


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


class CommandInput(BaseModel):
    exe: str
    args: list[str] = []
    env: dict[str, str] = {}
    cwd: str | None = None
    conda_env: str | None = None


class SchemeInput(BaseModel):
    config_source: str
    required_devices: list[str] = []
    command: CommandInput
    memory_mb: dict[str, int] = {}


class ModelDefInput(BaseModel):
    name: str
    mode: str                              # config.validate 校验 Chat/Embedding/Reranker
    port: int = Field(ge=1, le=65535)
    auto_start: bool = False
    aliases: list[str]                     # 非空(validate)
    schemes: list[SchemeInput]             # 非空(validate)


def _to_model_config(body: ModelDefInput) -> ModelConfig:
    """Pydantic 输入 → frozen ModelConfig。设备名 _norm_device(小写+strip)归一化,
    与 YAML 导入一致(否则对不上 DeviceMonitor)。重复 config_source → ValueError(→ 422)。"""
    schemes: dict[str, Scheme] = {}
    for s in body.schemes:
        if s.config_source in schemes:
            raise ValueError(f"duplicate scheme config_source '{s.config_source}'")
        schemes[s.config_source] = Scheme(
            config_source=s.config_source,
            required_devices=frozenset(_norm_device(d) for d in s.required_devices),
            command=Command(exe=s.command.exe, args=tuple(s.command.args),
                            env=dict(s.command.env), cwd=s.command.cwd, conda_env=s.command.conda_env),
            memory_mb={_norm_device(k): v for k, v in s.memory_mb.items()},
        )
    return ModelConfig(
        primary_name=body.name,
        aliases=tuple(body.aliases),
        mode=body.mode,
        port=body.port,
        auto_start=body.auto_start,
        schemes=schemes,
    )


def _create_model(cfg: AppConfig, body: ModelDefInput) -> AppConfig:
    """fn: AppConfig→AppConfig。name 已存在 → ModelExists(→ 409)。"""
    if body.name in cfg.models:
        raise ModelExists(body.name)
    return replace(cfg, models={**cfg.models, body.name: _to_model_config(body)})


def _update_model(cfg: AppConfig, name: str, body: ModelDefInput) -> AppConfig:
    """fn: 全量替换 name 处定义。不存在 → ModelNotFound(→ 404);body.name≠name → ValueError(改名,→ 422)。"""
    if name not in cfg.models:
        raise ModelNotFound(name)
    if body.name != name:
        raise ValueError(f"rename not supported (path '{name}' != body '{body.name}'); delete + create instead")
    return replace(cfg, models={**cfg.models, name: _to_model_config(body)})


def _delete_model(cfg: AppConfig, name: str) -> AppConfig:
    """fn: 删 name。不存在 → ModelNotFound(→ 404)。"""
    if name not in cfg.models:
        raise ModelNotFound(name)
    return replace(cfg, models={k: v for k, v in cfg.models.items() if k != name})


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


def _routing_served(primary: str, cfg: AppConfig) -> list[str]:
    """操作触及的模型若当前 ROUTING,返回其 served name(aliases[0]);用于 PUT 的 restart 提示。
    DELETE 的 ROUTING 拦截在端点处(404/409 之前)。"""
    from llm_manager import state
    from llm_manager.state import ModelStatus
    if state.get_status(primary) == ModelStatus.ROUTING:
        return [cfg.models[primary].aliases[0]]
    return []


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
        # log_level 已归重启类(L1):reload 仅刷快照,不再热重配 logging。
        cfg = _store(request).reload()
        rf = _restart_fields(cfg, _boot(request))
        return {"needs_restart": bool(rf), "restart_fields": rf, "serving": _serving()}

    @api.get("/config/restart-status")
    def restart_status(request: Request) -> dict:
        cfg = _store(request).snapshot()
        rf = _restart_fields(cfg, _boot(request))
        return {"needs_restart": bool(rf), "restart_fields": rf, "serving": _serving()}

    @api.get("/config/models")
    def list_model_defs(request: Request) -> list[dict]:
        cfg = _store(request).snapshot()
        return [{"name": name, "mode": m.mode, "port": m.port, "auto_start": m.auto_start,
                 "aliases": list(m.aliases), "schemes": list(m.schemes)}
                for name, m in cfg.models.items()]

    @api.post("/config/models", status_code=201)
    def create_model_def(request: Request, body: ModelDefInput) -> dict:
        db = _db(request)
        store = _store(request)
        try:
            mutate_appconfig(db, lambda c: _create_model(c, body))
        except ModelExists:
            raise HTTPException(409, f"model '{body.name}' already exists")
        except ConfigValidationFailed as e:
            raise HTTPException(422, detail=e.errors)
        except ValueError as e:
            raise HTTPException(422, detail=str(e))
        store.reload()
        return {"affected_routing": [], "hint": None}      # 新模型必未路由

    @api.get("/config/models/{name}")
    def get_model_def(name: str, request: Request) -> dict:
        cfg = _store(request).snapshot()
        if name not in cfg.models:
            raise HTTPException(404, f"model '{name}' not found")
        m = cfg.models[name]
        return {"name": name, "mode": m.mode, "port": m.port, "auto_start": m.auto_start,
                "aliases": list(m.aliases),
                "schemes": [{"config_source": s.config_source,
                             "required_devices": sorted(s.required_devices),
                             "command": {"exe": s.command.exe, "args": list(s.command.args),
                                         "env": s.command.env, "cwd": s.command.cwd,
                                         "conda_env": s.command.conda_env},
                             "memory_mb": dict(s.memory_mb)}
                            for s in m.schemes.values()]}

    @api.put("/config/models/{name}")
    def put_model_def(name: str, request: Request, body: ModelDefInput) -> dict:
        db = _db(request)
        store = _store(request)
        try:
            new_cfg = mutate_appconfig(db, lambda c: _update_model(c, name, body))
        except ModelNotFound:
            raise HTTPException(404, f"model '{name}' not found")
        except ConfigValidationFailed as e:
            raise HTTPException(422, detail=e.errors)
        except ValueError as e:
            raise HTTPException(422, detail=str(e))
        store.reload()
        affected = _routing_served(name, new_cfg)
        return {"affected_routing": affected, "hint": "restart_model" if affected else None}

    @api.delete("/config/models/{name}")
    def delete_model_def(name: str, request: Request) -> dict:
        store = _store(request)
        cfg = store.snapshot()
        if name not in cfg.models:
            raise HTTPException(404, f"model '{name}' not found")
        from llm_manager import state
        from llm_manager.state import ModelStatus
        if state.get_status(name) == ModelStatus.ROUTING:
            raise HTTPException(409, f"model '{name}' is routing; stop it before deleting")
        try:
            mutate_appconfig(_db(request), lambda c: _delete_model(c, name))
        except ModelNotFound:
            raise HTTPException(404, f"model '{name}' not found")
        store.reload()
        return {"affected_routing": [], "hint": None}
