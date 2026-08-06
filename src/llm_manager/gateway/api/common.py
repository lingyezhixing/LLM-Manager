"""/api/* 子路由共享的每请求访问器与 SSE 帧格式化。"""

from __future__ import annotations

from fastapi import Request
from pydantic import BaseModel

from llm_manager.data.config_store import ConfigStore
from llm_manager.data.persistence import Db


def get_db(request: Request) -> Db:
    """db(create_app lifespan 注入)。"""
    return request.app.state.db


def get_config_store(request: Request) -> ConfigStore:
    """config_store(读穿快照源)。"""
    return request.app.state.config_store


def sse_frame(payload: BaseModel) -> str:
    """SSE ``data:`` 帧(JSON 序列化)——models/devices/logs 三个流端点共用。"""
    return f"data: {payload.model_dump_json()}\n\n"
