"""/api/* 子路由共享的每请求访问器与 SSE 帧格式化。"""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from pydantic import BaseModel

from llm_manager.config import AppConfig
from llm_manager.data.config_store import ConfigStore
from llm_manager.data.persistence import Db


def get_db(request: Request) -> Db:
    """db(create_app lifespan 注入)。"""
    return request.app.state.db


def get_config_store(request: Request) -> ConfigStore:
    """config_store(读穿快照源)。"""
    return request.app.state.config_store


def db_size_bytes(request: Request) -> int | None:
    """DB 文件大小(不存在 → None)。system_info 与 storage-stats 共用。"""
    path = Path(str(getattr(request.app.state, "resolved_db", "data/llm_manager.db")))
    return path.stat().st_size if path.exists() else None


def sse_frame(payload: BaseModel) -> str:
    """SSE ``data:`` 帧(JSON 序列化)——models/devices/logs 三个流端点共用。"""
    return f"data: {payload.model_dump_json()}\n\n"


# ---------- config 写回结果(config_api + tools_api 共享)----------
# restart 检测:对比 snapshot.program 的 host/port/claude_settings_path/log_level 与
# app.state.boot_program(启动期捕获)。WOL/Claude 配置不在 RESTART_FIELDS → 恒不触发重启。

RESTART_FIELDS = ("host", "port", "claude_settings_path", "log_level")


def boot_program(request: Request) -> dict:
    return request.app.state.boot_program


def restart_fields(snapshot: AppConfig, boot: dict) -> list[str]:
    return [f for f in RESTART_FIELDS if str(getattr(snapshot.program, f)) != str(boot.get(f))]


def serving_models() -> list[str]:
    """当前正在服务(ROUTING 且 pending>0)的模型——restart 会中断它们。"""
    from llm_manager import state

    return [n for n in state.routing_names() if state.pending_count(n) > 0]


def config_write_result(request: Request, cfg: AppConfig) -> dict:
    """写回/查询的共享响应:needs_restart/restart_fields/serving。"""
    rf = restart_fields(cfg, boot_program(request))
    return {"needs_restart": bool(rf), "restart_fields": rf, "serving": serving_models()}
