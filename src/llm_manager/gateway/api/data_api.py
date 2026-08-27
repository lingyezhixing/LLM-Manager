"""数据管理 API:存储统计、孤立模型、删除模型数据。

孤立判定:models.original_name ∉ 已配置名集合(本地名 ∪ 服务商名 ∪ 云目录全名;
usage/runtime 记录的均为 primary_name)。见 `_configured_names`。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from llm_manager.data import logs as _logs
from llm_manager.data import persistence as _p
from llm_manager.gateway.api.common import (
    db_size_bytes,
    get_config_store,
    get_db,
)


def _configured_names(cfg) -> set[str]:
    """已配置名集合 = 本地模型名 ∪ 服务商名 ∪ 云目录全名(孤儿判定用)。"""
    names = set(cfg.models.keys())
    providers = getattr(cfg, "cloud_providers", {})
    for pname, p in providers.items():
        names.add(pname)
        for cm in p.models:
            names.add(f"{pname}/{cm.model_name}")
    return names


def register_data_routes(api: APIRouter) -> None:
    @api.get("/data/storage-stats")
    def storage_stats(request: Request) -> dict:
        db = get_db(request)
        cfg = get_config_store(request).snapshot()
        s = _p.storage_stats(
            db, configured=_configured_names(cfg), size_bytes=db_size_bytes(request)
        )
        log_sessions, log_lines = _logs.log_counts(db)
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
        names = _p.orphaned_models(db, _configured_names(cfg))
        return {"orphaned_models": names, "count": len(names)}

    @api.delete("/data/models/{name}")
    def delete_model_data(request: Request, name: str) -> dict:
        db = get_db(request)
        cfg = get_config_store(request).snapshot()
        if name in _configured_names(cfg):
            raise HTTPException(400, f"模型「{name}」仍在配置中,无法删除")
        if not _p.delete_model_data(db, name):
            raise HTTPException(404, f"未知模型:{name}")
        return {"deleted": name}
