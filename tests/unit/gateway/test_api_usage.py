"""GET /api/usage/session + GET /api/usage/series (token time-series)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from llm_manager.data import session
from llm_manager.data.persistence import open_db, record_usage
from llm_manager.gateway.api.usage import register_usage_routes


def _app(db=None) -> FastAPI:
    app = FastAPI()
    api = APIRouter(prefix="/api")
    register_usage_routes(api)
    app.include_router(api)
    app.state.db = db if db is not None else open_db(Path(":memory:"))
    return app


def test_usage_session_returns_totals() -> None:
    session._reset()
    session.add(100, 50, 30, 70)
    with TestClient(_app()) as c:
        r = c.get("/api/usage/session")
    assert r.status_code == 200
    j = r.json()
    assert isinstance(j["started_at"], (int, float))
    assert j["started_at"] > 0
    assert j["input_tokens"] == 100
    assert j["output_tokens"] == 50
    assert j["cache_hit"] == 30
    assert j["cache_miss"] == 70
    assert j["hit_rate"] == 0.3


def test_usage_series_endpoint_custom_range(tmp_path) -> None:
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", start=9, end=10, input_tokens=5, output_tokens=5, cache_n=0, prompt_n=5)
    record_usage(db, "m1", start=69, end=70, input_tokens=3, output_tokens=3, cache_n=0, prompt_n=3)
    record_usage(db, "m2", start=19, end=20, input_tokens=2, output_tokens=2, cache_n=0, prompt_n=2)
    with TestClient(_app(db)) as c:
        r = c.get("/api/usage/series?start=0&end=120")
    assert r.status_code == 200
    j = r.json()
    assert j["buckets"] == [0, 60]
    assert j["total"] == [14, 6]
    assert j["models"]["m1"] == [10, 6]
    assert j["models"]["m2"] == [4, 0]


def test_usage_series_endpoint_preset_returns_aligned_shape() -> None:
    with TestClient(_app()) as c:
        r = c.get("/api/usage/series?range=10m")
    assert r.status_code == 200
    j = r.json()
    assert len(j["buckets"]) == len(j["total"]) >= 1
    for series in j["models"].values():
        assert len(series) == len(j["buckets"])
