"""GET /api/usage/session + GET /api/usage/series(token 时间序列)。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from llm_manager.data.persistence import open_db
from llm_manager.data.usage import _reset_counters as _reset
from llm_manager.data.usage import record_usage
from llm_manager.data.usage import session_add as add
from llm_manager.gateway.api.usage import register_usage_routes


def _app(db=None, cfg=None) -> FastAPI:
    app = FastAPI()
    api = APIRouter(prefix="/api")
    register_usage_routes(api)
    app.include_router(api)
    app.state.db = db if db is not None else open_db(Path(":memory:"))
    app.state.started_at = 123.0  # 生产由 create_app 设置(与 /api/system/info 单源)
    if cfg is not None:

        class _Stub:
            def __init__(self, c):
                self._c = c

            def snapshot(self):
                return self._c

        app.state.config_store = _Stub(cfg)
    return app


def test_usage_session_returns_totals() -> None:
    _reset()
    add(100, 50, 30, 70)
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
    assert j["total_cost"] == 0.0  # 无 config_store(测试 stub)→ 计费降级为 0,不影响 token 面板


def test_usage_session_total_cost_since_start(tmp_path) -> None:
    """本次启动消耗 = 窗口 [started_at, now) 的成本;started_at 之前的请求不计数。"""
    import time

    from llm_manager.config import (
        AppConfig,
        Command,
        ModelConfig,
        Pricing,
        PricingTier,
        ProgramConfig,
        Scheme,
    )

    db = open_db(tmp_path / "t.db")
    now = time.time()
    record_usage(
        db,
        "m1",
        start=123.0,
        end=now - 0.5,
        input_tokens=1000,
        output_tokens=500,
        cache_n=0,
        prompt_n=1000,
    )  # 窗内(started_at=123.0 之后)
    record_usage(
        db,
        "m1",
        start=10.0,
        end=100.0,
        input_tokens=1000,
        output_tokens=500,
        cache_n=0,
        prompt_n=1000,
    )  # 窗外(上一进程,< started_at)
    cfg = AppConfig(
        program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
        models={
            "m1": ModelConfig(
                aliases=("m1",),
                mode="Chat",
                port=1,
                auto_start=False,
                schemes={"s": Scheme("s", frozenset({"gpu"}), Command(exe="x"), {"gpu": 1})},
                pricing=Pricing(
                    tiers=(PricingTier(tier_index=1, input_price=3.0, output_price=9.0),)
                ),
            ),
        },
        wol=None,
        claude_configs={},
    )
    with TestClient(_app(db, cfg)) as c:
        r = c.get("/api/usage/session")
    assert r.status_code == 200
    j = r.json()
    expected = (1000 * 3.0 + 500 * 9.0) / 1_000_000  # 仅窗内请求计费
    assert abs(j["total_cost"] - expected) < 1e-9


def test_usage_series_endpoint_custom_range(tmp_path) -> None:
    # 2h 跨度 → _bucket_for_span 返回 600s 桶(共 12 个)
    db = open_db(tmp_path / "t.db")
    record_usage(
        db, "m1", start=99, end=100, input_tokens=5, output_tokens=5, cache_n=0, prompt_n=0
    )
    record_usage(
        db, "m1", start=699, end=700, input_tokens=3, output_tokens=3, cache_n=0, prompt_n=0
    )
    record_usage(
        db, "m2", start=199, end=200, input_tokens=2, output_tokens=2, cache_n=0, prompt_n=0
    )
    with TestClient(_app(db)) as c:
        r = c.get("/api/usage/series?start=0&end=7200")
    assert r.status_code == 200
    j = r.json()
    assert len(j["buckets"]) == 12  # 7200 / 600
    assert j["models"]["m1"][0] == 10 and j["models"]["m1"][1] == 6  # end=100→b0, end=700→b600
    assert j["models"]["m2"][0] == 4  # end=200→b0
    assert j["total"][0] == 14 and j["total"][1] == 6
    assert sum(j["total"]) == 20


def test_usage_series_endpoint_preset_returns_aligned_shape() -> None:
    with TestClient(_app()) as c:
        r = c.get("/api/usage/series?period=10m")
    assert r.status_code == 200
    j = r.json()
    assert len(j["buckets"]) == len(j["total"]) >= 1
    for series in j["models"].values():
        assert len(series) == len(j["buckets"])


def test_usage_summary_endpoint_aggregates_range(tmp_path) -> None:
    db = open_db(tmp_path / "t.db")
    record_usage(
        db, "m1", start=5.0, end=10.0, input_tokens=100, output_tokens=20, cache_n=60, prompt_n=40
    )
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
    record_usage(
        db, "m1", start=5.0, end=10.0, input_tokens=60, output_tokens=20, cache_n=40, prompt_n=20
    )
    record_usage(
        db, "m1", start=12.0, end=15.0, input_tokens=40, output_tokens=10, cache_n=20, prompt_n=20
    )
    record_usage(
        db, "m2", start=15.0, end=18.0, input_tokens=50, output_tokens=10, cache_n=0, prompt_n=50
    )
    with TestClient(_app(db)) as c:
        r = c.get("/api/usage/by-model?start=0&end=100")
    assert r.status_code == 200
    j = r.json()
    assert len(j) == 2
    assert j[0]["model"] == "m1"
    assert j[0]["share"] == 100 / 150
    assert j[0]["hit_rate"] == 0.6
    assert j[0]["latency_ms"] == 4000.0  # AVG(5s, 3s) = 4s
    assert j[1]["model"] == "m2"


def test_usage_by_model_endpoint_empty_returns_empty_list(tmp_path) -> None:
    db = open_db(tmp_path / "t.db")
    with TestClient(_app(db)) as c:
        r = c.get("/api/usage/by-model?start=0&end=100")
    assert r.status_code == 200
    assert r.json() == []


def test_usage_cost_endpoint_tier_and_hourly(tmp_path):
    from llm_manager.config import (
        AppConfig,
        Command,
        ModelConfig,
        Pricing,
        PricingTier,
        ProgramConfig,
        Scheme,
    )

    db = open_db(tmp_path / "t.db")
    record_usage(
        db,
        "m1",
        start=5.0,
        end=10.0,
        input_tokens=1000,
        output_tokens=500,
        cache_n=0,
        prompt_n=1000,
    )
    from llm_manager.data.usage import record_runtime_end, record_runtime_start

    _seg = record_runtime_start(db, "m2", start=0.0)
    record_runtime_end(db, _seg, end=3600.0)
    cfg = AppConfig(
        program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
        models={
            "m1": ModelConfig(
                aliases=("m1",),
                mode="Chat",
                port=1,
                auto_start=False,
                schemes={"s": Scheme("s", frozenset({"gpu"}), Command(exe="x"), {"gpu": 1})},
                pricing=Pricing(
                    tiers=(PricingTier(tier_index=1, input_price=3.0, output_price=9.0),)
                ),
            ),
            "m2": ModelConfig(
                aliases=("m2",),
                mode="Chat",
                port=2,
                auto_start=False,
                schemes={"s": Scheme("s", frozenset({"gpu"}), Command(exe="x"), {"gpu": 1})},
                pricing=Pricing(pricing_type="hourly", hourly_price=10.0),
            ),
        },
        wol=None,
        claude_configs={},
    )
    with TestClient(_app(db, cfg)) as c:
        r = c.get("/api/usage/cost?start=0&end=7200")
    assert r.status_code == 200
    j = r.json()
    names = {row["model"]: row for row in j["by_model"]}
    assert abs(j["total_cost"] - (((1000 * 3.0 + 500 * 9.0) / 1_000_000) + 10.0)) < 1e-9
    assert names["m1"]["pricing_type"] == "tier"
    assert names["m2"]["pricing_type"] == "hourly"


def test_usage_cost_series_endpoint_returns_buckets(tmp_path):
    from llm_manager.config import (
        AppConfig,
        Command,
        ModelConfig,
        Pricing,
        PricingTier,
        ProgramConfig,
        Scheme,
    )

    db = open_db(tmp_path / "t.db")
    record_usage(
        db, "m1", start=9, end=10, input_tokens=1000, output_tokens=0, cache_n=0, prompt_n=1000
    )
    cfg = AppConfig(
        program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
        models={
            "m1": ModelConfig(
                aliases=("m1",),
                mode="Chat",
                port=1,
                auto_start=False,
                schemes={"s": Scheme("s", frozenset({"gpu"}), Command(exe="x"), {"gpu": 1})},
                pricing=Pricing(tiers=(PricingTier(tier_index=1, input_price=3.0),)),
            ),
        },
        wol=None,
        claude_configs={},
    )
    with TestClient(_app(db, cfg)) as c:
        r = c.get("/api/usage/cost-series?start=0&end=7200")
    assert r.status_code == 200
    j = r.json()
    assert len(j["buckets"]) == len(j["total"])
    assert abs(j["models"]["m1"][0] - 1000 * 3.0 / 1_000_000) < 1e-9


def test_usage_session_cost_split(tmp_path):
    import time

    from llm_manager.config import (
        AppConfig,
        CloudModel,
        CloudProvider,
        Command,
        ModelConfig,
        Pricing,
        PricingTier,
        ProgramConfig,
        Scheme,
    )

    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", 123.0, time.time(), 1000, 500, 0, 1000)
    record_usage(db, "ds/x", 123.0, time.time(), 2000, 0, 0, 2000, source="cloud")
    cfg = AppConfig(
        program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
        models={
            "m1": ModelConfig(
                aliases=("m1",),
                mode="Chat",
                port=1,
                schemes={"s": Scheme("s", frozenset({"gpu"}), Command(exe="x"), {"gpu": 1})},
                pricing=Pricing(
                    tiers=(PricingTier(tier_index=1, input_price=3.0, output_price=9.0),)
                ),
            )
        },
        wol=None,
        claude_configs={},
        cloud_providers={
            "ds": CloudProvider(
                name="ds",
                models=(
                    CloudModel(
                        model_name="x", tiers_base=(PricingTier(tier_index=1, input_price=1.0),)
                    ),
                ),
            )
        },
    )
    with TestClient(_app(db, cfg)) as c:
        j = c.get("/api/usage/session").json()
    assert j["total_cost"] > 0
    assert j["local_cost"] > 0 and j["cloud_cost"] > 0
    assert abs(j["local_cost"] + j["cloud_cost"] - j["total_cost"]) < 1e-9


def test_usage_session_source_param(tmp_path):
    """session 端点亦属 /api/usage/* 全系 source 参数;过滤后三拆恒等。"""
    import time

    from llm_manager.config import (
        AppConfig,
        CloudModel,
        CloudProvider,
        Command,
        ModelConfig,
        Pricing,
        PricingTier,
        ProgramConfig,
        Scheme,
    )

    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", 123.0, time.time(), 1000, 0, 0, 1000)
    record_usage(db, "ds/x", 123.0, time.time(), 2000, 0, 0, 2000, source="cloud")
    cfg = AppConfig(
        program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
        models={
            "m1": ModelConfig(
                aliases=("m1",),
                mode="Chat",
                port=1,
                schemes={"s": Scheme("s", frozenset({"gpu"}), Command(exe="x"), {"gpu": 1})},
                pricing=Pricing(tiers=(PricingTier(tier_index=1, input_price=3.0),)),
            )
        },
        wol=None,
        claude_configs={},
        cloud_providers={
            "ds": CloudProvider(
                name="ds",
                models=(
                    CloudModel(
                        model_name="x", tiers_base=(PricingTier(tier_index=1, input_price=1.0),)
                    ),
                ),
            )
        },
    )
    with TestClient(_app(db, cfg)) as c:
        jc = c.get("/api/usage/session?source=cloud").json()
        assert jc["local_cost"] == 0.0 and jc["cloud_cost"] == jc["total_cost"]
        jl = c.get("/api/usage/session?source=local").json()
        assert jl["cloud_cost"] == 0.0 and jl["local_cost"] == jl["total_cost"]
        assert c.get("/api/usage/session?source=bogus").status_code == 422


def test_usage_cost_endpoint_source_param(tmp_path):
    from llm_manager.config import (
        AppConfig,
        CloudModel,
        CloudProvider,
        Command,
        ModelConfig,
        Pricing,
        PricingTier,
        ProgramConfig,
        Scheme,
    )

    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", 5, 10, 1000, 0, 0, 1000)
    record_usage(db, "ds/x", 5, 10, 2000, 0, 0, 2000, source="cloud")
    cfg = AppConfig(
        program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
        models={
            "m1": ModelConfig(
                aliases=("m1",),
                mode="Chat",
                port=1,
                schemes={"s": Scheme("s", frozenset({"gpu"}), Command(exe="x"), {"gpu": 1})},
                pricing=Pricing(tiers=(PricingTier(tier_index=1, input_price=3.0),)),
            )
        },
        wol=None,
        claude_configs={},
        cloud_providers={
            "ds": CloudProvider(
                name="ds",
                models=(
                    CloudModel(
                        model_name="x", tiers_base=(PricingTier(tier_index=1, input_price=1.0),)
                    ),
                ),
            )
        },
    )
    with TestClient(_app(db, cfg)) as c:
        j = c.get("/api/usage/cost?start=0&end=100").json()
    names = {r["model"]: r for r in j["by_model"]}
    assert names["m1"]["source"] == "local"
    assert names["ds/x"]["source"] == "cloud"
    assert j["local_cost"] > 0 and j["cloud_cost"] > 0
    assert abs(j["local_cost"] + j["cloud_cost"] - j["total_cost"]) < 1e-9
    with TestClient(_app(db, cfg)) as c:
        jc = c.get("/api/usage/cost?start=0&end=100&source=cloud").json()
    assert all(r["source"] == "cloud" for r in jc["by_model"])
    # 非法 source → 422(Pydantic Literal)
    assert c.get("/api/usage/cost?start=0&end=100&source=bogus").status_code == 422
