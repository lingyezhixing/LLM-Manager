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
    # 2h span → _bucket_for_span returns 600s buckets (12 of them)
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", start=99, end=100, input_tokens=5, output_tokens=5, cache_n=0, prompt_n=0)
    record_usage(db, "m1", start=699, end=700, input_tokens=3, output_tokens=3, cache_n=0, prompt_n=0)
    record_usage(db, "m2", start=199, end=200, input_tokens=2, output_tokens=2, cache_n=0, prompt_n=0)
    with TestClient(_app(db)) as c:
        r = c.get("/api/usage/series?start=0&end=7200")
    assert r.status_code == 200
    j = r.json()
    assert len(j["buckets"]) == 12                       # 7200 / 600
    assert j["models"]["m1"][0] == 10 and j["models"]["m1"][1] == 6   # end=100→b0, end=700→b600
    assert j["models"]["m2"][0] == 4                     # end=200→b0
    assert j["total"][0] == 14 and j["total"][1] == 6
    assert sum(j["total"]) == 20


def test_usage_series_endpoint_preset_returns_aligned_shape() -> None:
    with TestClient(_app()) as c:
        r = c.get("/api/usage/series?range=10m")
    assert r.status_code == 200
    j = r.json()
    assert len(j["buckets"]) == len(j["total"]) >= 1
    for series in j["models"].values():
        assert len(series) == len(j["buckets"])


def test_usage_summary_endpoint_aggregates_range(tmp_path) -> None:
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", start=5.0, end=10.0, input_tokens=100, output_tokens=20, cache_n=60, prompt_n=40)
    with TestClient(_app(db)) as c:
        r = c.get("/api/usage/summary?start=0&end=100")
    assert r.status_code == 200
    j = r.json()
    assert j["request_count"] == 1
    assert j["input_tokens"] == 100
    assert j["output_tokens"] == 20
    assert j["cache_hit"] == 60
    assert j["cache_miss"] == 40
    assert j["hit_rate"] == 0.6


def test_usage_summary_endpoint_empty_returns_zeros(tmp_path) -> None:
    db = open_db(tmp_path / "t.db")
    with TestClient(_app(db)) as c:
        r = c.get("/api/usage/summary?start=0&end=100")
    assert r.status_code == 200
    j = r.json()
    assert j["request_count"] == 0
    assert j["hit_rate"] == 0.0


def test_usage_by_model_endpoint_groups_and_shares(tmp_path) -> None:
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", start=5.0, end=10.0, input_tokens=100, output_tokens=20, cache_n=60, prompt_n=40)
    record_usage(db, "m2", start=15.0, end=20.0, input_tokens=50, output_tokens=10, cache_n=0, prompt_n=50)
    with TestClient(_app(db)) as c:
        r = c.get("/api/usage/by-model?start=0&end=100")
    assert r.status_code == 200
    j = r.json()
    assert len(j) == 2
    assert j[0]["model"] == "m1"
    assert j[0]["share"] == 100 / 150
    assert j[0]["hit_rate"] == 0.6
    assert j[1]["model"] == "m2"


def test_usage_by_model_endpoint_empty_returns_empty_list(tmp_path) -> None:
    db = open_db(tmp_path / "t.db")
    with TestClient(_app(db)) as c:
        r = c.get("/api/usage/by-model?start=0&end=100")
    assert r.status_code == 200
    assert r.json() == []
