"""System config write-back API: GET/PUT /api/config*, /api/system/info, restart 检测.

校验层 = Pydantic 请求模型(FastAPI 自动 422)。写经 set_settings(多键原子)→ store.reload()。
restart 检测:对比 snapshot.program 的 host/port/claude_settings_path/log_level 与 app.state.boot_program(启动期捕获)。
读穿仅 system_settings 影响的消费方(idle 循环/tray/logging)——lifecycle/catalog/models 随模型 CRUD。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from llm_manager.config import AppConfig, Command, ModelConfig, Pricing, PricingTier, Scheme, _norm_device
from llm_manager.data import logs as _logs
from llm_manager.data.config_store import (
    ConfigValidationFailed,
    ModelExists,
    ModelNotFound,
    mutate_appconfig,
    set_settings,
)
from llm_manager.gateway.api.common import get_config_store, get_db
from llm_manager.tray import claude

try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version
    try:
        _VERSION = _pkg_version("llm-manager")
    except PackageNotFoundError:
        _VERSION = "unknown"
except Exception:
    _VERSION = "unknown"

logger = logging.getLogger(__name__)

_RESTART_FIELDS = ("host", "port", "claude_settings_path", "log_level")

# 退出码 81 契约:生产监督器与 Dev-Backend.bat 均在其上重启
RESTART_EXIT_CODE = 81


class ProgramUpdate(BaseModel):
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    alive_time: int | None = Field(default=None, ge=0)
    log_level: str | None = None
    claude_settings_path: str | None = None


class WolUpdate(BaseModel):
    broadcast_address: str = Field(min_length=1)   # B8:必填非空(清除走 DELETE,不靠空串半残配置)
    mac_address: str = Field(min_length=1)

class ClaudeConfigsUpdate(BaseModel):
    configs: dict[str, dict[str, str]]

class LogRetentionUpdate(BaseModel):
    """日志保留规则:恒生效的两个参数(按时间保留 N 天 + 按条数保留 N 条,系统与模型日志同时适用)。"""
    days: int | None = Field(default=None, ge=1)
    count: int | None = Field(default=None, ge=1)


class ClaudeApplyRequest(BaseModel):
    name: str


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


class PricingTierInput(BaseModel):
    tier_index: int
    min_input: int | None = 0
    max_input: int | None = None
    min_output: int | None = 0
    max_output: int | None = None
    input_price: float = 0.0
    output_price: float = 0.0
    cache_write_price: float = 0.0
    cache_read_price: float = 0.0


class PricingInput(BaseModel):
    pricing_type: Literal["tier", "hourly"] = "tier"
    hourly_price: float = 0.0
    support_cache: bool = False
    tiers: list[PricingTierInput] = []


class ModelDefInput(BaseModel):
    name: str
    mode: str                              # config.validate 校验 Chat/Embedding/Reranker
    port: int = Field(ge=1, le=65535)
    auto_start: bool = False
    aliases: list[str]                     # 非空(validate)
    schemes: list[SchemeInput]             # 非空(validate)
    pricing: PricingInput = Field(default_factory=PricingInput)


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
    pricing = Pricing(
        pricing_type=body.pricing.pricing_type,
        hourly_price=body.pricing.hourly_price,
        support_cache=body.pricing.support_cache,
        tiers=tuple(PricingTier(
            tier_index=t.tier_index, min_input=t.min_input, max_input=t.max_input,
            min_output=t.min_output, max_output=t.max_output,
            input_price=t.input_price, output_price=t.output_price,
            cache_write_price=t.cache_write_price,
            cache_read_price=t.cache_read_price) for t in body.pricing.tiers),
    )
    return ModelConfig(
        primary_name=body.name,
        aliases=tuple(body.aliases),
        mode=body.mode,
        port=body.port,
        auto_start=body.auto_start,
        schemes=schemes,
        pricing=pricing,
    )


def _pricing_dict(p):
    return {
        "pricing_type": p.pricing_type,
        "hourly_price": p.hourly_price,
        "support_cache": p.support_cache,
        "tiers": [
            {"tier_index": t.tier_index, "min_input": t.min_input, "max_input": t.max_input,
             "min_output": t.min_output, "max_output": t.max_output,
             "input_price": t.input_price, "output_price": t.output_price,
             "cache_write_price": t.cache_write_price,
             "cache_read_price": t.cache_read_price}
            for t in p.tiers
        ],
    }


def _create_model(cfg: AppConfig, body: ModelDefInput) -> AppConfig:
    """fn: AppConfig→AppConfig。name 已存在 → ModelExists(→ 409)。"""
    if body.name in cfg.models:
        raise ModelExists(body.name)
    return replace(cfg, models={**cfg.models, body.name: _to_model_config(body)})


def _update_model(cfg: AppConfig, name: str, body: ModelDefInput) -> AppConfig:
    """fn: 全量替换 name 处定义。
    - 不存在 → ModelNotFound(→ 404)
    - body.name == name → 全量替换该定义
    - body.name ≠ name(改名)→ 换字典 key;新名已存在 → ModelExists(→ 409)。
    数据层迁移(models.original_name/log_sessions)由端点经 post_write 处理,此纯函数不碰 DB。"""
    if name not in cfg.models:
        raise ModelNotFound(name)
    if body.name == name:
        return replace(cfg, models={**cfg.models, name: _to_model_config(body)})
    if body.name in cfg.models:
        raise ModelExists(body.name)
    new_models = {k: v for k, v in cfg.models.items() if k != name}
    new_models[body.name] = _to_model_config(body)
    return replace(cfg, models=new_models)


def _rename_migrator(old: str, new: str) -> "Callable":
    """构造 post_write 回调:把模型身份从 old 迁到 new(改名 + 迁移时)。

    - models.original_name(用量/成本/运行时锚点):old → new
    - log_sessions.model_name:old → new
    - log_sessions.alias(日志显示的服务名快照):同步为改名后配置的 aliases[0]——
      日志页按别名显示,不同步会残留旧别名快照(出现「两个名字」)。
    """
    def migrate(db, _old_cfg, new_cfg):
        db.conn.execute("UPDATE models SET original_name=? WHERE original_name=?", (new, old))
        new_alias = new_cfg.models[new].aliases[0] if new_cfg.models[new].aliases else new
        db.conn.execute(
            "UPDATE log_sessions SET model_name=?, alias=? WHERE model_name=?",
            (new, new_alias, old),
        )
    return migrate


def _delete_old_sessions(old: str) -> "Callable":
    """构造 post_write 回调:不迁移改名时,删除旧名日志会话。

    与 delete_model_def 一致(日志绑定定义:旧身份废弃 → 删日志;请求记录留孤立)。
    匹配 model_name 或 alias ∈ {old} ∪ 旧别名(别名从 old_cfg 取改名前的值)。
    在改名同事务内执行(commit 前),与配置写原子。
    """
    def drop(db, old_cfg, _new_cfg):
        aliases = old_cfg.models[old].aliases if old in old_cfg.models else ()
        terms = {old, *aliases}
        if not terms:
            return
        ph = ",".join("?" * len(terms))
        db.conn.execute(
            f"DELETE FROM log_sessions WHERE model_name IN ({ph}) OR alias IN ({ph})",
            (*terms, *terms),
        )
    return drop


def _alias_migrator(primary: str) -> "Callable":
    """构造 post_write 回调:改别名(非改名)时,把日志 alias 快照同步为新的 aliases[0]。

    日志页按别名显示;改别名若不迁移日志,旧日志残留旧别名快照(日志页显示旧名,
    出现「两个名字」)。与改名迁移(_rename_migrator)对齐:别名变更即同步快照。"""
    def sync(db, _old_cfg, new_cfg):
        new_alias = new_cfg.models[primary].aliases[0]
        db.conn.execute(
            "UPDATE log_sessions SET alias=? WHERE model_name=?",
            (new_alias, primary),
        )
    return sync


def _delete_model(cfg: AppConfig, name: str) -> AppConfig:
    """fn: 删 name。不存在 → ModelNotFound(→ 404)。"""
    if name not in cfg.models:
        raise ModelNotFound(name)
    return replace(cfg, models={k: v for k, v in cfg.models.items() if k != name})


def _boot(request: Request) -> dict:
    return request.app.state.boot_program


def _restart_fields(snapshot, boot: dict) -> list[str]:
    return [f for f in _RESTART_FIELDS if str(getattr(snapshot.program, f)) != str(boot.get(f))]


def _serving() -> list[str]:
    """当前正在服务(ROUTING 且 pending>0)的模型——restart 会中断它们。"""
    from llm_manager import state
    return [n for n in state.routing_names() if state.pending_count(n) > 0]


def _config_write_result(request: Request, cfg: AppConfig) -> dict:
    """写回/查询的共享响应:needs_restart/restart_fields/serving(原 5 处内联)。"""
    rf = _restart_fields(cfg, _boot(request))
    return {"needs_restart": bool(rf), "restart_fields": rf, "serving": _serving()}


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
        started_at = getattr(request.app.state, "started_at", None) or time.time()
        db_path = Path(str(getattr(request.app.state, "resolved_db", "data/llm_manager.db")))
        return {
            "version": _VERSION,
            "started_at": started_at,
            "uptime_s": max(0.0, time.time() - started_at),
            "db_size_bytes": db_path.stat().st_size if db_path.exists() else None,
        }

    @api.get("/config")
    def get_config(request: Request) -> dict:
        cfg = get_config_store(request).snapshot()
        boot = _boot(request)
        p = cfg.program
        return {
            "program": {
                "host": p.host, "port": p.port, "alive_time": p.alive_time,
                "log_level": p.log_level,
                "claude_settings_path": p.claude_settings_path,
            },
            "wol": ({"broadcast_address": cfg.wol.broadcast_address,
                     "mac_address": cfg.wol.mac_address} if cfg.wol is not None else None),
            "claude": cfg.claude_configs,
            "logs": {"days": p.log_retention_days, "count": p.log_retention_count},
            "restart_fields": _restart_fields(cfg, boot),
        }

    @api.put("/config/program")
    def put_program(request: Request, body: ProgramUpdate) -> dict:
        updates: dict[str, str] = {}
        for f in ("host", "log_level", "claude_settings_path"):
            v = getattr(body, f)
            if v is not None:
                updates[f] = v
        for f in ("port", "alive_time"):
            v = getattr(body, f)
            if v is not None:
                updates[f] = str(v)
        if updates:
            set_settings(get_db(request), updates)
        cfg = get_config_store(request).reload()
        return _config_write_result(request, cfg)

    @api.put("/config/wol")
    def put_wol(request: Request, body: WolUpdate) -> dict:
        # 两字段均必填非空(WolUpdate min_length=1);清除走 DELETE。原 `is not None` 是死分支
        # (Pydantic 必填 str 恒非 None)。整组写,与 delete_wol 对称。
        set_settings(get_db(request), {
            "wol_broadcast": body.broadcast_address,
            "wol_mac": body.mac_address,
        })
        cfg = get_config_store(request).reload()
        return _config_write_result(request, cfg)

    @api.delete("/config/wol")
    def delete_wol(request: Request) -> dict:
        """清除 WOL 配置(删双键 → snapshot.wol=None,托盘动作提示未配置)。
        与 put_wol 对称:WOL 是双键一对,清除必须整对删,不留孤儿键。"""
        db = get_db(request)
        with db.write_lock:
            db.conn.execute("DELETE FROM system_settings WHERE key IN ('wol_broadcast', 'wol_mac')")
            db.conn.commit()
        cfg = get_config_store(request).reload()
        return _config_write_result(request, cfg)

    @api.put("/config/claude")
    def put_claude(request: Request, body: ClaudeConfigsUpdate) -> dict:
        set_settings(get_db(request), {"claude_configs": json.dumps(body.configs, ensure_ascii=False)})
        cfg = get_config_store(request).reload()
        return _config_write_result(request, cfg)

    @api.post("/config/claude/apply")
    def apply_claude_preset(request: Request, body: ClaudeApplyRequest) -> dict:
        """把已存预设写入 Claude settings.json(非破坏,仅更新 env 键)。404 未知预设;400 未配置路径。"""
        cfg = get_config_store(request).snapshot()
        preset = (cfg.claude_configs or {}).get(body.name)
        if preset is None:
            raise HTTPException(404, f"preset '{body.name}' not found")
        path = cfg.program.claude_settings_path
        if not path:
            raise HTTPException(400, "未配置 Claude settings 路径")
        try:
            claude.apply_preset(Path(path), dict(preset))
        except OSError as e:
            raise HTTPException(500, f"写入 settings.json 失败:{e}")
        return {"applied": body.name}

    @api.get("/config/claude/current")
    def current_claude_preset(request: Request) -> dict:
        """探测当前生效预设(按 ANTHROPIC_BASE_URL 子串匹配);未配置路径/读失败 → "(未知)"。"""
        cfg = get_config_store(request).snapshot()
        path = Path(cfg.program.claude_settings_path) if cfg.program.claude_settings_path else None
        current = claude.detect_current_preset(path, dict(cfg.claude_configs)) if path else "(未知)"
        return {"current": current}

    @api.put("/config/logs")
    def put_logs(request: Request, body: LogRetentionUpdate) -> dict:
        updates: dict[str, str] = {}
        if body.days is not None:
            updates["log_retention_days"] = str(body.days)
        if body.count is not None:
            updates["log_retention_count"] = str(body.count)
        if updates:
            set_settings(get_db(request), updates)
        get_config_store(request).reload()                  # 日志规则已并入 AppConfig 快照;reload 保持新鲜
        return _config_write_result(request, get_config_store(request).snapshot())

    @api.get("/config/restart-status")
    def restart_status(request: Request) -> dict:
        return _config_write_result(request, get_config_store(request).snapshot())

    @api.post("/config/restart", status_code=202)
    async def restart_app(request: Request) -> dict:
        """请求优雅重启:置 app.state.restart_requested;有 uvicorn server → 后台延迟翻
        should_exit(让 202 先冲刷),worker 优雅跑完 lifespan 收尾后以 81 退出。
        无 server(dev --reload)→ 0.5s 后 os._exit(81)(dev 无监督器,需手动重启)。
        生产路径:内置 parent 监督器接住 81 拉起全新 worker(不依赖外部 bat/sh)。"""
        request.app.state.restart_requested = True
        server = getattr(request.app.state, "uvicorn_server", None)
        if server is not None:
            async def _delayed_exit() -> None:
                await asyncio.sleep(0.5)
                server.should_exit = True
            asyncio.create_task(_delayed_exit())
        else:
            async def _dev_exit() -> None:
                await asyncio.sleep(0.5)
                os._exit(RESTART_EXIT_CODE)
            asyncio.create_task(_dev_exit())
        return {}

    @api.get("/config/models")
    def list_model_defs(request: Request) -> list[dict]:
        cfg = get_config_store(request).snapshot()
        return [{"name": name, "mode": m.mode, "port": m.port, "auto_start": m.auto_start,
                 "aliases": list(m.aliases), "schemes": list(m.schemes)}
                for name, m in cfg.models.items()]

    @api.post("/config/models", status_code=201)
    def create_model_def(request: Request, body: ModelDefInput) -> dict:
        db = get_db(request)
        store = get_config_store(request)
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
        cfg = get_config_store(request).snapshot()
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
                            for s in m.schemes.values()],
                "pricing": _pricing_dict(m.pricing)}

    @api.put("/config/models/{name}")
    def put_model_def(
        name: str, request: Request, body: ModelDefInput, migrate_data: bool = False
    ) -> dict:
        db = get_db(request)
        store = get_config_store(request)
        is_rename = body.name != name
        if is_rename:
            # 运行中拦截:活跃态改名会与 state(primary_name keyed)/lifecycle 错位
            from llm_manager import state
            from llm_manager.state import ModelStatus
            st = state.get_status(name)
            if st not in (ModelStatus.STOPPED, ModelStatus.FAILED):
                raise HTTPException(409, f"model '{name}' is {st.value}; stop it before renaming")
            # UNIQUE 预检:迁移时新名不得已被孤立数据占用(否则 UPDATE models 撞 UNIQUE)
            if migrate_data and db.conn.execute(
                "SELECT 1 FROM models WHERE original_name = ?", (body.name,)
            ).fetchone():
                raise HTTPException(
                    422,
                    f"new name '{body.name}' is occupied by orphaned data; "
                    "clean it in data management first",
                )
        if is_rename:
            # 迁移=数据+日志跟新名;不迁移=旧身份废弃,删旧日志(与 delete_model_def 一致),
            # 请求记录留孤立。两者均在改名同事务内经 post_write 原子执行。
            post = _rename_migrator(name, body.name) if migrate_data else _delete_old_sessions(name)
        else:
            # 非改名但 aliases[0] 变更:同步日志别名快照(日志页按别名显示,不残留旧名)。
            # 变更判定用改前快照(store.snapshot);sync 用 post_write 传入的 new_cfg 取新别名。
            old_cfg = store.snapshot()
            if body.aliases and name in old_cfg.models \
                    and body.aliases[0] != old_cfg.models[name].aliases[0]:
                post = _alias_migrator(name)
            else:
                post = None
        try:
            new_cfg = mutate_appconfig(db, lambda c: _update_model(c, name, body), post_write=post)
        except ModelNotFound:
            raise HTTPException(404, f"model '{name}' not found")
        except ModelExists:
            raise HTTPException(409, f"model '{body.name}' already exists")
        except ConfigValidationFailed as e:
            raise HTTPException(422, detail=e.errors)
        except ValueError as e:
            raise HTTPException(422, detail=str(e))
        store.reload()
        # 改名时模型已停(运行中拦截),affected 必为空;非改名维持原 _routing_served 语义
        primary_for_hint = body.name if is_rename else name
        affected = _routing_served(primary_for_hint, new_cfg)
        return {"affected_routing": affected, "hint": "restart_model" if affected else None}

    @api.delete("/config/models/{name}")
    def delete_model_def(name: str, request: Request) -> dict:
        store = get_config_store(request)
        cfg = store.snapshot()
        if name not in cfg.models:
            raise HTTPException(404, f"model '{name}' not found")
        from llm_manager import state
        from llm_manager.state import ModelStatus
        if state.get_status(name) == ModelStatus.ROUTING:
            raise HTTPException(409, f"model '{name}' is routing; stop it before deleting")
        aliases = cfg.models[name].aliases   # 快照仍在,先取别名(删日志匹配用)
        try:
            mutate_appconfig(get_db(request), lambda c: _delete_model(c, name))
        except ModelNotFound:
            raise HTTPException(404, f"model '{name}' not found")
        store.reload()
        # 设计:删定义 = 连带删日志 + 保留请求记录(成为孤立模型,由数据管理页清理)。
        # best-effort:日志删除失败不影响定义删除结果。
        try:
            _logs.delete_model_sessions(get_db(request), name, aliases)
        except Exception:
            logger.warning("delete model '%s' log sessions failed", name, exc_info=True)
        return {"affected_routing": [], "hint": None}
