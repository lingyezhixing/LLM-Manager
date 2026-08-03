"""Data management API: storage stats, orphaned models, delete model data.

迁移自 legacy(api_server.py 1048-1115 + data_manager.py 数据管理段)。孤立判定:
models.original_name ∉ AppConfig.models.keys()(usage/runtime 记录的均为 primary_name)。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from llm_manager.data import persistence as _p
from llm_manager.gateway.api.common import get_config_store, get_db


def register_data_routes(api: APIRouter) -> None:

    @api.get("/data/storage-stats")
    def storage_stats(request: Request) -> dict:
        db = get_db(request)
        db_path = Path(str(getattr(request.app.state, "resolved_db", "data/llm_manager.db")))
        size = db_path.stat().st_size if db_path.exists() else None
        cfg = get_config_store(request).snapshot()
        s = _p.storage_stats(db, configured=set(cfg.models.keys()), size_bytes=size)
        log_sessions, log_lines = _p.log_counts(db)
        return {
            "size_bytes": s.size_bytes,
            "total_requests": s.total_requests,
            "total_models_with_data": s.total_models_with_data,
            "models_data": {
                name: {"request_count": st.request_count, "has_runtime_data": st.has_runtime_data}
                for name, st in s.models_data.items()
            },
            "log_sessions": log_sessions,
            "log_lines": log_lines,
        }

    @api.get("/data/models/orphaned")
    def orphaned_models(request: Request) -> dict:
        db = get_db(request)
        cfg = get_config_store(request).snapshot()
        names = _p.orphaned_models(db, set(cfg.models.keys()))
        return {"orphaned_models": names, "count": len(names)}

    @api.delete("/data/models/{name}")
    def delete_model_data(request: Request, name: str) -> dict:
        db = get_db(request)
        cfg = get_config_store(request).snapshot()
        if name in cfg.models:
            raise HTTPException(400, f"模型「{name}」仍在配置中,无法删除")
        if not _p.delete_model_data(db, name):
            raise HTTPException(404, f"未知模型:{name}")
        return {"deleted": name}
