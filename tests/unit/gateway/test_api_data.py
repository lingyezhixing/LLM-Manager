"""Data management API: GET /api/data/storage-stats, GET /api/data/models/orphaned,
DELETE /api/data/models/{name}. Mirror of test_api_usage.py fixtures."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from llm_manager.data.persistence import (
    log_insert_lines,
    log_start_session,
    open_db,
    record_usage,
)
from llm_manager.gateway.api.data_api import register_data_routes


def _app(db=None, cfg=None, resolved_db: str | None = None) -> FastAPI:
    app = FastAPI()
    api = APIRouter(prefix="/api")
    register_data_routes(api)
    app.include_router(api)
    app.state.db = db if db is not None else open_db(Path(":memory:"))
    app.state.resolved_db = resolved_db
    if cfg is not None:
        class _Stub:
            def __init__(self, c): self._c = c
            def snapshot(self): return self._c
        app.state.config_store = _Stub(cfg)
    return app


def test_storage_stats_empty() -> None:
    with TestClient(_app(cfg=SimpleNamespace(models={}))) as c:
        r = c.get("/api/data/storage-stats")
    assert r.status_code == 200
    j = r.json()
    assert j["size_bytes"] is None
    assert j["total_requests"] == 0
    assert j["total_models_with_data"] == 0
    assert j["models_data"] == {}
    assert j["log_sessions"] == 0
    assert j["log_lines"] == 0


def test_storage_stats_with_data(tmp_path) -> None:
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", 1, 2, input_tokens=5, output_tokens=5, cache_n=0, prompt_n=0)
    record_usage(db, "m1", 3, 4, input_tokens=1, output_tokens=1, cache_n=0, prompt_n=0)
    record_usage(db, "m2", 1, 2, input_tokens=2, output_tokens=2, cache_n=0, prompt_n=0)
    cfg = SimpleNamespace(models={"m1": object(), "m3": object()})
    with TestClient(_app(db, cfg, resolved_db=str(tmp_path / "t.db"))) as c:
        r = c.get("/api/data/storage-stats")
    assert r.status_code == 200
    j = r.json()
    assert j["size_bytes"] == (tmp_path / "t.db").stat().st_size
    assert j["total_requests"] == 3
    assert j["total_models_with_data"] == 2
    assert j["models_data"]["m1"]["request_count"] == 2
    assert j["models_data"]["m3"] == {"request_count": 0, "has_runtime_data": False}  # 配置但无数据
    assert set(j["models_data"]) == {"m1", "m2", "m3"}


def test_storage_stats_includes_log_counts(tmp_path) -> None:
    db = open_db(tmp_path / "t.db")
    sid = log_start_session(db, "system", None, None, 1000.0)
    log_insert_lines(db, sid, [(1, 1000.1, "sys", "info", "a"), (2, 1000.2, "sys", "info", "b")])
    cfg = SimpleNamespace(models={})
    with TestClient(_app(db, cfg, resolved_db=str(tmp_path / "t.db"))) as c:
        r = c.get("/api/data/storage-stats")
    assert r.status_code == 200
    j = r.json()
    assert j["log_sessions"] == 1
    assert j["log_lines"] == 2


def test_orphaned_returns_diff() -> None:
    db = open_db(Path(":memory:"))
    record_usage(db, "kept", 1, 2, input_tokens=1, output_tokens=1, cache_n=0, prompt_n=0)
    record_usage(db, "gone", 1, 2, input_tokens=1, output_tokens=1, cache_n=0, prompt_n=0)
    cfg = SimpleNamespace(models={"kept": object()})
    with TestClient(_app(db, cfg)) as c:
        r = c.get("/api/data/models/orphaned")
    assert r.status_code == 200
    j = r.json()
    assert j == {"orphaned_models": ["gone"], "count": 1}


def test_delete_orphaned_model() -> None:
    db = open_db(Path(":memory:"))
    record_usage(db, "gone", 1, 2, input_tokens=1, output_tokens=1, cache_n=0, prompt_n=0)
    with TestClient(_app(db, SimpleNamespace(models={}))) as c:
        r = c.delete("/api/data/models/gone")
        assert r.status_code == 200
        assert r.json() == {"deleted": "gone"}
        assert c.delete("/api/data/models/gone").status_code == 404  # 已删 → 未知


def test_delete_refuses_configured_model() -> None:
    db = open_db(Path(":memory:"))
    record_usage(db, "kept", 1, 2, input_tokens=1, output_tokens=1, cache_n=0, prompt_n=0)
    cfg = SimpleNamespace(models={"kept": object()})
    with TestClient(_app(db, cfg)) as c:
        r = c.delete("/api/data/models/kept")
    assert r.status_code == 400
    assert "仍在配置" in r.json()["detail"]
