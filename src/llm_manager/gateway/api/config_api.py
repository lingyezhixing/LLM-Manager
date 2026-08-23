"""System config write-back API: GET/PUT /api/config*, /api/system/info, restart 检测.

校验层 = Pydantic 请求模型(FastAPI 自动 422)。写经 set_settings(多键原子)→ store.reload()。
restart 检测:对比 snapshot.program 的 host/port/claude_settings_path/log_level 与 app.state.boot_program(启动期捕获)。
读穿仅 system_settings 影响的消费方(idle 循环/tray/logging)——lifecycle/models 随模型 CRUD。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import replace

from fastapi import APIRouter, HTTPException, Request

from llm_manager.config import AppConfig
from llm_manager.data import logs as _logs
from llm_manager.data.config_store import (
    ConfigValidationFailed,
    ModelExists,
    ModelNotFound,
    mutate_appconfig,
    set_settings,
)
from llm_manager.gateway.api.common import (
    boot_program,
    config_write_result,
    db_size_bytes,
    get_config_store,
    get_db,
    restart_fields,
    trigger_restart,
)
from llm_manager.gateway.api.config_schemas import (
    LogRetentionUpdate,
    ModelDefInput,
    ProgramUpdate,
    _to_model_config,
)
from llm_manager.version import get_version

logger = logging.getLogger(__name__)


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


def _rename_migrator(old: str, new: str) -> Callable:
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


def _delete_old_sessions(old: str) -> Callable:
    """构造 post_write 回调:不迁移改名时,删除旧名日志会话。

    与 delete_model_def 一致(日志绑定定义:旧身份废弃 → 删日志;请求记录留孤立)。
    匹配 model_name 或 alias ∈ {old} ∪ 旧别名(别名从 old_cfg 取改名前的值)。
    在改名同事务内执行(commit 前),与配置写原子。
    """

    def drop(db, old_cfg, _new_cfg):
        aliases = old_cfg.models[old].aliases if old in old_cfg.models else ()
        # 在 mutate_appconfig 的同一写事务内执行(不 commit,由调用方统一提交)。
        _logs._delete_sessions_locked(db, {old, *aliases})

    return drop


def _alias_migrator(primary: str) -> Callable:
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
        return {
            "version": get_version(),
            "started_at": started_at,
            "uptime_s": max(0.0, time.time() - started_at),
            "db_size_bytes": db_size_bytes(request),
        }

    @api.get("/config")
    def get_config(request: Request) -> dict:
        cfg = get_config_store(request).snapshot()
        boot = boot_program(request)
        p = cfg.program
        return {
            "program": {
                "host": p.host,
                "port": p.port,
                "alive_time": p.alive_time,
                "log_level": p.log_level,
                "claude_settings_path": p.claude_settings_path,
            },
            # 当前运行实例的 program(启动期捕获):「保存前预检」与「恢复运行值回退」的
            # 依据(RESTART_FIELDS 比较以它为准,而非库值)。
            "running_program": {
                "host": boot.get("host", ""),
                "port": int(boot.get("port", "0")),
                "alive_time": p.alive_time,
                "log_level": boot.get("log_level", "INFO"),
                "claude_settings_path": boot.get("claude_settings_path", ""),
            },
            "wol": (
                {"broadcast_address": cfg.wol.broadcast_address, "mac_address": cfg.wol.mac_address}
                if cfg.wol is not None
                else None
            ),
            "claude": cfg.claude_configs,
            "logs": {"days": p.log_retention_days, "count": p.log_retention_count},
            "restart_fields": restart_fields(cfg, boot),
        }

    @api.put("/config/program")
    def put_program(request: Request, body: ProgramUpdate, dry_run: bool = False) -> dict:
        """dry_run=true:只算不写——以当前快照 + 请求体模拟保存后的 program,返回同形
        config_write_result(restart_fields/serving),供前端「预检→确认→落库」流(先检测
        冲突再落地,取消=零副作用);其余校验(Pydantic 422)与真实写一致。"""
        updates: dict[str, str] = {}
        for f in ("host", "log_level", "claude_settings_path"):
            v = getattr(body, f)
            if v is not None:
                updates[f] = v
        for f in ("port", "alive_time"):
            v = getattr(body, f)
            if v is not None:
                updates[f] = str(v)
        store = get_config_store(request)
        if dry_run:
            sim_kwargs: dict[str, str | int] = {}
            for k, v in updates.items():
                sim_kwargs[k] = int(v) if k in ("port", "alive_time") else v
            sim = replace(store.snapshot(), program=replace(store.snapshot().program, **sim_kwargs))
            return config_write_result(request, sim)
        if updates:
            set_settings(get_db(request), updates)
        cfg = store.reload()
        return config_write_result(request, cfg)

    @api.put("/config/logs")
    def put_logs(request: Request, body: LogRetentionUpdate) -> dict:
        updates: dict[str, str] = {}
        if body.days is not None:
            updates["log_retention_days"] = str(body.days)
        if body.count is not None:
            updates["log_retention_count"] = str(body.count)
        if updates:
            set_settings(get_db(request), updates)
        get_config_store(request).reload()  # 日志规则已并入 AppConfig 快照;reload 保持新鲜
        return config_write_result(request, get_config_store(request).snapshot())

    @api.get("/config/restart-status")
    def restart_status(request: Request) -> dict:
        return config_write_result(request, get_config_store(request).snapshot())

    @api.post("/config/restart", status_code=202)
    async def restart_app(request: Request) -> dict:
        """请求优雅重启:置 restart_requested → worker 退出 81 → parent 拉起新 worker。"""
        trigger_restart(request)
        return {}

    @api.get("/config/models")
    def list_model_defs(request: Request) -> list[dict]:
        cfg = get_config_store(request).snapshot()
        return [
            {
                "name": name,
                "mode": m.mode,
                "port": m.port,
                "auto_start": m.auto_start,
                "aliases": list(m.aliases),
                "schemes": list(m.schemes),
            }
            for name, m in cfg.models.items()
        ]

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
        return {"affected_routing": [], "hint": None}  # 新模型必未路由

    @api.get("/config/models/{name}")
    def get_model_def(name: str, request: Request) -> dict:
        cfg = get_config_store(request).snapshot()
        if name not in cfg.models:
            raise HTTPException(404, f"model '{name}' not found")
        m = cfg.models[name]
        return {
            "name": name,
            "mode": m.mode,
            "port": m.port,
            "auto_start": m.auto_start,
            "aliases": list(m.aliases),
            "schemes": [
                {
                    "config_source": s.config_source,
                    "required_devices": sorted(s.required_devices),
                    "command": {
                        "exe": s.command.exe,
                        "args": list(s.command.args),
                        "env": s.command.env,
                        "cwd": s.command.cwd,
                        "conda_env": s.command.conda_env,
                    },
                    "memory_mb": dict(s.memory_mb),
                }
                for s in m.schemes.values()
            ],
            "pricing": m.pricing.to_dict(),
        }

    @api.put("/config/models/{name}")
    def put_model_def(
        name: str,
        request: Request,
        body: ModelDefInput,
        migrate_data: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """dry_run=true:只算不写——相同校验存在性(404/409/422) + 模拟保存后的
        affected_routing,供「预检→确认→落库」流;post_write/写库/reload 全部跳过。"""
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
            if (
                migrate_data
                and db.conn.execute(
                    "SELECT 1 FROM models WHERE original_name = ?", (body.name,)
                ).fetchone()
            ):
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
            if (
                body.aliases
                and name in old_cfg.models
                and body.aliases[0] != old_cfg.models[name].aliases[0]
            ):
                post = _alias_migrator(name)
            else:
                post = None
        try:
            if dry_run:
                # 预检:纯函数模拟 + validate(与 mutate_appconfig 同样的校验,不碰 DB)
                new_cfg = _update_model(store.snapshot(), name, body)
                from llm_manager.config import validate as _validate

                errors = _validate(new_cfg)
                if errors:
                    raise ConfigValidationFailed(errors)
            else:
                new_cfg = mutate_appconfig(
                    db, lambda c: _update_model(c, name, body), post_write=post
                )
        except ModelNotFound:
            raise HTTPException(404, f"model '{name}' not found")
        except ModelExists:
            raise HTTPException(409, f"model '{body.name}' already exists")
        except ConfigValidationFailed as e:
            raise HTTPException(422, detail=e.errors)
        except ValueError as e:
            raise HTTPException(422, detail=str(e))
        if dry_run:
            primary_for_hint = body.name if is_rename else name
            affected = _routing_served(primary_for_hint, new_cfg)
            return {"affected_routing": affected, "hint": "restart_model" if affected else None}
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

        if state.get_status(name) not in (ModelStatus.STOPPED, ModelStatus.FAILED):
            raise HTTPException(
                409, f"model '{name}' is {state.get_status(name).value}; stop it before deleting"
            )
        aliases = cfg.models[name].aliases  # 快照仍在,先取别名(删日志匹配用)
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
