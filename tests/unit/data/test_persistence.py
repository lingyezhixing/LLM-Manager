"""DB schema/migration + 存储管理(data/persistence.py)与 usage 域聚合
(record_usage / record_runtime / usage_series / usage_cost 等,data/usage.py)的测试;
日志 SQL 存储层测试在 tests/unit/data/test_logs.py(与 src 布局对齐)。"""

import sqlite3
import threading
from pathlib import Path

import pytest

from llm_manager.data.persistence import (
    delete_model_data,
    open_db,
    orphaned_models,
    storage_stats,
)
from llm_manager.data.usage import (
    record_runtime_end,
    record_runtime_start,
    record_usage,
    resolve_model_id,
    runtime_heartbeat_live,
    tier_cost,
    usage_by_model,
    usage_cost,
    usage_cost_series,
    usage_series,
    usage_summary,
)


@pytest.fixture(autouse=True)
def _clear_live_segments():
    """record_runtime_start 把段 id 加入模块全局 _live_segments(心跳/关闭用)。
    清空它防跨测试残留污染(配对 start/end 的测试本身干净,不配对的会残留)。"""
    from llm_manager.data.usage import _live_segments

    _live_segments.clear()
    yield
    _live_segments.clear()


def test_open_db_sets_pragmas_and_creates_schema(tmp_path):
    db = open_db(tmp_path / "t.db")
    mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    tables = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "models" in tables
    assert "model_requests" in tables


def test_record_usage_writes_start_end_tokens(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(
        db, "m1", start=100.0, end=200.0, input_tokens=5, output_tokens=10, cache_n=1, prompt_n=4
    )
    row = db.conn.execute(
        "SELECT start_time, end_time, input_tokens FROM model_requests"
    ).fetchone()
    assert row["start_time"] == 100.0
    assert row["end_time"] == 200.0
    assert row["input_tokens"] == 5


def test_record_usage_auto_creates_model_round_trips(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(
        db,
        "Qwen3-4B",
        start=1.0,
        end=2.0,
        input_tokens=100,
        output_tokens=50,
        cache_n=20,
        prompt_n=80,
    )
    rows = db.conn.execute(
        "SELECT r.input_tokens, r.output_tokens FROM model_requests r "
        "JOIN models m ON r.model_id = m.id WHERE m.original_name = 'Qwen3-4B'"
    ).fetchall()
    assert len(rows) == 1
    assert (rows[0]["input_tokens"], rows[0]["output_tokens"]) == (100, 50)


def test_resolve_model_id_is_stable(tmp_path):
    db = open_db(tmp_path / "t.db")
    a = resolve_model_id(db, "M")
    b = resolve_model_id(db, "M")
    assert a == b


def test_concurrent_writes_serialized_by_lock(tmp_path):
    db = open_db(tmp_path / "t.db")
    errors = []

    def write():
        try:
            for _ in range(20):
                record_usage(
                    db,
                    "M",
                    start=0.0,
                    end=0.1,
                    input_tokens=1,
                    output_tokens=1,
                    cache_n=0,
                    prompt_n=1,
                )
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=write) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    n = db.conn.execute("SELECT COUNT(*) AS n FROM model_requests").fetchone()["n"]
    assert n == 80


def test_usage_series_buckets_per_model_and_total(tmp_path):
    db = open_db(tmp_path / "t.db")
    # bucket=60,区间 [0,120) → 桶 [0, 60];时间键为 end_time
    record_usage(db, "m1", start=9, end=10, input_tokens=5, output_tokens=5, cache_n=0, prompt_n=5)
    record_usage(db, "m1", start=69, end=70, input_tokens=3, output_tokens=3, cache_n=0, prompt_n=3)
    record_usage(db, "m2", start=19, end=20, input_tokens=2, output_tokens=2, cache_n=0, prompt_n=2)
    result = usage_series(db, start_ts=0, end_ts=120, bucket_seconds=60)
    assert result.buckets == [0, 60]
    assert result.models["m1"] == [10, 6]  # 5+5 在桶 0,3+3 在桶 1
    assert result.models["m2"] == [4, 0]  # 2+2 在桶 0,无 → 补 0
    assert result.total == [14, 6]


def test_usage_series_empty_range_returns_no_buckets(tmp_path):
    db = open_db(tmp_path / "t.db")
    result = usage_series(db, start_ts=0, end_ts=0, bucket_seconds=60)
    assert result.buckets == []
    assert result.total == []


def test_usage_series_buckets_are_clock_aligned_not_start_relative(tmp_path):
    """桶与时钟对齐(bucket_seconds 的整数倍),与窗口起点无关——
    滑动窗口滚动图表而非重排每个请求。"""
    db = open_db(tmp_path / "t.db")
    record_usage(
        db, "m1", start=69, end=70, input_tokens=1, output_tokens=1, cache_n=0, prompt_n=0
    )  # end=70 → 绝对桶 60
    result = usage_series(db, start_ts=10, end_ts=130, bucket_seconds=60)  # 非对齐起点
    assert result.buckets == [0, 60, 120]  # 首个 = floor(10/60)*60 = 0
    assert result.models["m1"] == [0, 2, 0]  # end=70 → 桶 60 → 下标 1


def test_usage_summary_aggregates_half_open_range(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(
        db, "m1", start=5.0, end=10.0, input_tokens=100, output_tokens=20, cache_n=60, prompt_n=40
    )
    record_usage(
        db, "m1", start=15.0, end=20.0, input_tokens=50, output_tokens=10, cache_n=0, prompt_n=50
    )
    record_usage(
        db, "m2", start=25.0, end=30.0, input_tokens=10, output_tokens=5, cache_n=10, prompt_n=0
    )
    # 左闭右开 [0, 25):含 end=10,20;不含 end=30
    s = usage_summary(db, start_ts=0.0, end_ts=25.0)
    assert s.request_count == 2
    assert s.input_tokens == 150
    assert s.output_tokens == 30
    assert s.cache_hit == 60
    assert s.cache_miss == 90
    assert s.hit_rate == 60 / 150


def test_usage_summary_empty_range_returns_zeros(tmp_path):
    db = open_db(tmp_path / "t.db")
    s = usage_summary(db, start_ts=0.0, end_ts=10.0)
    assert s.request_count == 0
    assert s.input_tokens == 0
    assert s.cache_hit == 0
    assert s.hit_rate == 0.0


def test_usage_by_model_groups_orders_shares_and_latency(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(
        db, "m1", start=5.0, end=10.0, input_tokens=60, output_tokens=20, cache_n=40, prompt_n=20
    )  # 延迟 5s
    record_usage(
        db, "m1", start=12.0, end=15.0, input_tokens=40, output_tokens=10, cache_n=20, prompt_n=20
    )  # 延迟 3s
    record_usage(
        db, "m2", start=15.0, end=18.0, input_tokens=50, output_tokens=10, cache_n=0, prompt_n=50
    )  # 延迟 3s
    rows = usage_by_model(db, start_ts=0.0, end_ts=25.0)
    assert [r.model for r in rows] == ["m1", "m2"]  # 按 input 降序
    assert rows[0].input_tokens == 100
    assert rows[0].request_count == 2
    assert rows[0].cache_n == 60
    assert rows[0].share == 100 / 150
    assert rows[0].hit_rate == 0.6
    assert rows[0].latency_ms == 4000.0  # AVG(5s, 3s) = 4s
    assert rows[1].model == "m2"
    assert rows[1].request_count == 1
    assert rows[1].share == 50 / 150
    assert rows[1].hit_rate == 0.0
    assert rows[1].latency_ms == 3000.0


def test_usage_by_model_empty_returns_empty_list(tmp_path):
    db = open_db(tmp_path / "t.db")
    assert usage_by_model(db, start_ts=0.0, end_ts=10.0) == []


def test_open_db_creates_config_tables(tmp_path):
    db = open_db(tmp_path / "t.db")
    for t in (
        "system_settings",
        "model_defs",
        "model_aliases",
        "model_schemes",
        "pricing_tiers",
        "log_sessions",
        "log_lines",
    ):
        assert (
            db.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)
            ).fetchone()
            is not None
        )


def test_open_db_creates_model_runtime_table(tmp_path):
    db = open_db(tmp_path / "t.db")
    tables = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "model_runtime" in tables


def test_record_runtime_start_end_round_trip(tmp_path):
    db = open_db(tmp_path / "t.db")
    seg = record_runtime_start(db, "m1", start=100.0)
    record_runtime_end(db, seg, end=250.0)
    row = db.conn.execute(
        "SELECT start_time, end_time FROM model_runtime r JOIN models m ON r.model_id=m.id "
        "WHERE m.original_name='m1'"
    ).fetchone()
    assert row["start_time"] == 100.0
    assert row["end_time"] == 250.0


def test_record_runtime_end_closes_by_segment_id(tmp_path):
    """record_runtime_end 按 segment_id 关段(不再靠 end_time IS NULL 找最新)。
    开两段拿 id,只关指定段;另一段仍开;已关段再关幂等 no-op。"""
    db = open_db(tmp_path / "t.db")
    record_runtime_start(db, "m1", start=100.0)  # 段 1(id 不用,仅造"另一段仍开")
    seg2 = record_runtime_start(db, "m1", start=200.0)
    record_runtime_end(db, seg2, end=300.0)  # 按 id 只关 seg2
    rows = db.conn.execute(
        "SELECT start_time, end_time FROM model_runtime r JOIN models m ON r.model_id=m.id "
        "WHERE m.original_name='m1' ORDER BY start_time"
    ).fetchall()
    assert rows[0]["end_time"] is None  # seg1 仍开
    assert rows[1]["end_time"] == 300.0  # seg2 已关
    record_runtime_end(db, seg2, end=999.0)  # 幂等:seg2 已移出 _live_segments → no-op
    again = db.conn.execute("SELECT end_time FROM model_runtime WHERE id=?", (seg2,)).fetchone()
    assert again["end_time"] == 300.0  # 未被二次覆盖


def test_runtime_heartbeat_live_writes_end_time(tmp_path):
    """心跳把进行中运行段(_live_segments)的 end_time 推到 now;已结束段不动。"""
    db = open_db(tmp_path / "t.db")
    seg1 = record_runtime_start(db, "m1", start=100.0)
    record_runtime_end(db, seg1, end=200.0)
    record_runtime_start(db, "m2", start=300.0)  # 仍开(在 _live_segments)
    assert runtime_heartbeat_live(db, 500.0) == 1
    rows = db.conn.execute(
        "SELECT m.original_name AS name, r.end_time AS end_time FROM model_runtime r "
        "JOIN models m ON r.model_id=m.id ORDER BY r.start_time"
    ).fetchall()
    by_name = {r["name"]: r["end_time"] for r in rows}
    assert by_name["m2"] == 500.0  # 进行中 → end_time 推到心跳值
    assert by_name["m1"] == 200.0  # 已结束 → 精确值不动
    assert runtime_heartbeat_live(db, 600.0) == 1  # 仍只一条进行中


def test_open_db_has_no_last_active_column(tmp_path):
    """last_active 列已移除(解耦重构:end_time 由心跳直接维持,不再需独立心跳列)。"""
    db = open_db(tmp_path / "t.db")
    for tbl in ("log_sessions", "model_runtime"):
        cols = {r[1] for r in db.conn.execute(f"PRAGMA table_info({tbl})")}
        assert "last_active" not in cols


def test_tier_cost_no_cache_matches_and_divides_by_million(tmp_path):
    from llm_manager.config import Pricing, PricingTier

    db = open_db(tmp_path / "t.db")  # 未使用但保持风格一致  # noqa: F841
    pricing = Pricing(
        tiers=(
            PricingTier(
                tier_index=1, min_input=0, max_input=32768, input_price=3.0, output_price=9.0
            ),
        )
    )
    # 1000 input @ 3/M + 500 output @ 9/M = (3000 + 4500)/1e6
    assert tier_cost(pricing, 1000, 500, 0, 0) == (1000 * 3.0 + 500 * 9.0) / 1_000_000


def test_tier_cost_cache_formula(tmp_path):
    from llm_manager.config import Pricing, PricingTier

    # support_cache 是模型级开关(Pricing 上),缓存价仍在阶梯上
    pricing = Pricing(
        support_cache=True,
        tiers=(
            PricingTier(
                tier_index=1,
                min_input=0,
                max_input=None,
                input_price=3.0,
                output_price=9.0,
                cache_write_price=3.75,
                cache_read_price=0.3,
            ),
        ),
    )
    # cache_n*read + prompt_n*(input+write) + output*output, /1e6
    expected = (200 * 0.3 + 800 * (3.0 + 3.75) + 500 * 9.0) / 1_000_000
    assert tier_cost(pricing, 1000, 500, 200, 800) == expected


def test_tier_cost_cache_off_uses_plain_formula(tmp_path):
    """support_cache=False(默认)→ 即使阶梯带缓存价也走无缓存公式。"""
    from llm_manager.config import Pricing, PricingTier

    pricing = Pricing(
        tiers=(
            PricingTier(
                tier_index=1,
                min_input=0,
                max_input=None,
                input_price=3.0,
                output_price=9.0,
                cache_write_price=3.75,
                cache_read_price=0.3,
            ),
        )
    )
    expected = (1000 * 3.0 + 500 * 9.0) / 1_000_000  # 无缓存公式,忽略缓存价
    assert tier_cost(pricing, 1000, 500, 200, 800) == expected


def test_tier_cost_no_match_returns_zero(tmp_path):
    from llm_manager.config import Pricing, PricingTier

    pricing = Pricing(
        tiers=(
            PricingTier(
                tier_index=1, min_input=0, max_input=100, input_price=3.0, output_price=9.0
            ),
        )
    )
    assert tier_cost(pricing, 9999, 0, 0, 0) == 0.0  # 阶梯窗口之外


def test_tier_cost_min_zero_closed_min_nonzero_open(tmp_path):
    from llm_manager.config import Pricing, PricingTier

    pricing = Pricing(
        tiers=(
            PricingTier(
                tier_index=1, min_input=100, max_input=None, input_price=1.0, output_price=0.0
            ),
        )
    )
    assert tier_cost(pricing, 100, 0, 0, 0) == 0.0  # min=100(非零)→ 开区间 → 100 不包含
    assert tier_cost(pricing, 101, 0, 0, 0) == 101 * 1.0 / 1_000_000


def _cfg_with(pricing):
    from llm_manager.config import AppConfig, Command, ModelConfig, ProgramConfig, Scheme

    return AppConfig(
        program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
        models={
            "m1": ModelConfig(
                aliases=("m1",),
                mode="Chat",
                port=1,
                auto_start=False,
                schemes={"s": Scheme("s", frozenset({"gpu"}), Command(exe="x"), {"gpu": 1})},
                pricing=pricing,
            )
        },
        wol=None,
        claude_configs={},
    )


def test_usage_cost_tier_model_sums_requests(tmp_path):
    from llm_manager.config import Pricing, PricingTier

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
    record_usage(
        db, "m1", start=12.0, end=15.0, input_tokens=2000, output_tokens=0, cache_n=0, prompt_n=2000
    )
    cfg = _cfg_with(Pricing(tiers=(PricingTier(tier_index=1, input_price=3.0, output_price=9.0),)))
    s = usage_cost(db, cfg, start_ts=0.0, end_ts=100.0)
    expected = ((1000 * 3.0 + 500 * 9.0) + (2000 * 3.0)) / 1_000_000
    assert s.total_cost == expected
    assert s.by_model[0].model == "m1" and s.by_model[0].pricing_type == "tier"


def test_usage_cost_hourly_model_uses_runtime_overlap(tmp_path):
    from llm_manager.config import Pricing

    db = open_db(tmp_path / "t.db")
    seg = record_runtime_start(db, "m1", start=0.0)
    record_runtime_end(db, seg, end=7200.0)  # 运行 2 小时
    cfg = _cfg_with(Pricing(pricing_type="hourly", hourly_price=10.0))
    s = usage_cost(db, cfg, start_ts=0.0, end_ts=3600.0, now=9999.0)  # 窗口 = 1 小时
    assert s.total_cost == 10.0  # 1h × 10/h
    assert s.by_model[0].pricing_type == "hourly"


def test_usage_cost_open_session_uses_now(tmp_path):
    from llm_manager.config import Pricing

    db = open_db(tmp_path / "t.db")
    record_runtime_start(db, "m1", start=0.0)  # 永不关闭
    cfg = _cfg_with(Pricing(pricing_type="hourly", hourly_price=10.0))
    s = usage_cost(db, cfg, start_ts=0.0, end_ts=3600.0, now=3600.0)  # now 将会话截断到 1h
    assert s.total_cost == 10.0


def test_usage_cost_free_model_yields_zero_and_is_omitted(tmp_path):
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
    from llm_manager.config import AppConfig, Command, ModelConfig, ProgramConfig, Scheme

    cfg = AppConfig(
        program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
        models={
            "m1": ModelConfig(
                aliases=("m1",),
                mode="Chat",
                port=1,
                auto_start=False,
                schemes={"s": Scheme("s", frozenset({"gpu"}), Command(exe="x"), {"gpu": 1})},
            )
        },
        wol=None,
        claude_configs={},
    )
    s = usage_cost(db, cfg, start_ts=0.0, end_ts=100.0)
    assert s.total_cost == 0.0 and s.by_model == []


def test_usage_cost_series_buckets_tier_cost_by_end_time(tmp_path):
    from llm_manager.config import Pricing, PricingTier

    db = open_db(tmp_path / "t.db")
    record_usage(
        db, "m1", start=9, end=10, input_tokens=1000, output_tokens=0, cache_n=0, prompt_n=1000
    )
    record_usage(
        db, "m1", start=69, end=70, input_tokens=2000, output_tokens=0, cache_n=0, prompt_n=2000
    )
    cfg = _cfg_with(Pricing(tiers=(PricingTier(tier_index=1, input_price=3.0, output_price=0.0),)))
    res = usage_cost_series(db, cfg, start_ts=0, end_ts=120, bucket_seconds=60)
    assert res.buckets == [0, 60]
    assert res.models["m1"][0] == 1000 * 3.0 / 1_000_000  # end=10 → 桶 0
    assert res.models["m1"][1] == 2000 * 3.0 / 1_000_000  # end=70 → 桶 60
    assert res.total[0] == res.models["m1"][0]
    assert res.total[1] == res.models["m1"][1]


def test_usage_cost_series_hourly_spreads_across_buckets(tmp_path):
    from llm_manager.config import Pricing

    db = open_db(tmp_path / "t.db")
    seg = record_runtime_start(db, "m1", start=0.0)
    record_runtime_end(db, seg, end=120.0)  # 2 minutes loaded
    cfg = _cfg_with(Pricing(pricing_type="hourly", hourly_price=3600.0))  # 1 元/s
    res = usage_cost_series(db, cfg, start_ts=0, end_ts=120, bucket_seconds=60, now=9999.0)
    assert res.buckets == [0, 60]
    assert res.total == [60.0, 60.0]  # 每桶各 60s × 1 元/s


def test_usage_cost_series_hourly_join_by_model_id(tmp_path):
    """回归:hourly 批量查询 JOIN 须按 r.model_id=mm.id(曾误写 r.id=mm.id——单模型
    单段时两者 id 巧合相等而测试误过;第二运行段 id=2 对 model id=1 即可区分)。"""
    from llm_manager.config import Pricing

    db = open_db(tmp_path / "t.db")
    s1 = record_runtime_start(db, "m1", start=0.0)
    record_runtime_end(db, s1, end=60.0)
    s2 = record_runtime_start(db, "m1", start=60.0)  # 段 id=2 ≠ model id=1
    record_runtime_end(db, s2, end=120.0)
    cfg = _cfg_with(Pricing(pricing_type="hourly", hourly_price=3600.0))
    res = usage_cost_series(db, cfg, start_ts=0, end_ts=120, bucket_seconds=60, now=9999.0)
    assert res.total == [60.0, 60.0]  # 两段各 60s × 1 元/s;坏 JOIN 会丢第二段 → [60, 0]


def test_usage_cost_series_empty_range_returns_no_buckets(tmp_path):
    from llm_manager.config import Pricing

    db = open_db(tmp_path / "t.db")
    cfg = _cfg_with(Pricing())
    res = usage_cost_series(db, cfg, start_ts=0, end_ts=0, bucket_seconds=60)
    assert res.buckets == [] and res.total == [] and res.models == {}


def test_storage_stats_empty(tmp_path):
    db = open_db(tmp_path / "t.db")
    s = storage_stats(db, configured=set(), size_bytes=123)
    assert s.size_bytes == 123
    assert s.total_requests == 0
    assert s.total_models_with_data == 0
    assert s.models_data == {}


def test_storage_stats_counts_requests_and_runtime(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", 100, 200, input_tokens=5, output_tokens=5, cache_n=0, prompt_n=0)
    record_usage(db, "m1", 300, 400, input_tokens=1, output_tokens=1, cache_n=0, prompt_n=0)
    seg_m2 = record_runtime_start(db, "m2", 500)
    record_runtime_end(db, seg_m2, 600)
    s = storage_stats(db, configured=set(), size_bytes=99)
    assert s.total_requests == 2
    assert s.total_models_with_data == 2
    m1, m2 = s.models_data["m1"], s.models_data["m2"]
    assert m1.request_count == 2 and not m1.has_runtime_data
    assert m2.request_count == 0 and m2.has_runtime_data


def test_storage_stats_union_includes_configured_without_data(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "used", 1, 2, input_tokens=1, output_tokens=1, cache_n=0, prompt_n=0)
    s = storage_stats(db, configured={"used", "cfg-only"}, size_bytes=None)
    assert set(s.models_data) == {"used", "cfg-only"}
    assert s.models_data["cfg-only"].request_count == 0
    assert not s.models_data["cfg-only"].has_runtime_data
    assert s.total_models_with_data == 1  # 配置但无数据的不计入


def test_orphaned_models_is_config_diff(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "kept", 1, 2, input_tokens=1, output_tokens=1, cache_n=0, prompt_n=0)
    record_usage(db, "gone", 1, 2, input_tokens=1, output_tokens=1, cache_n=0, prompt_n=0)
    assert orphaned_models(db, {"kept"}) == ["gone"]
    assert orphaned_models(db, {"kept", "gone"}) == []


def test_delete_model_data_cascades_and_unknown_returns_false(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "gone", 1, 2, input_tokens=1, output_tokens=1, cache_n=0, prompt_n=0)
    record_runtime_start(db, "gone", 1)
    assert delete_model_data(db, "gone") is True
    assert delete_model_data(db, "gone") is False  # 已删 → 未知
    assert db.conn.execute("SELECT COUNT(*) FROM models").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM model_requests").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM model_runtime").fetchone()[0] == 0


def test_delete_model_data_vacuum_compacts_pages(tmp_path):
    db = open_db(tmp_path / "t.db")
    for i in range(200):
        record_usage(db, f"m{i}", 1, 2, input_tokens=1, output_tokens=1, cache_n=0, prompt_n=0)
    before = db.conn.execute("PRAGMA page_count").fetchone()[0]
    for i in range(200):
        assert delete_model_data(db, f"m{i}") is True
    after = db.conn.execute("PRAGMA page_count").fetchone()[0]
    assert after < before  # VACUUM 压缩后页数显著减少


def test_delete_model_data_in_memory_db_no_crash():
    db = open_db(Path(":memory:"))
    record_usage(db, "m1", 1, 2, input_tokens=1, output_tokens=1, cache_n=0, prompt_n=0)
    assert delete_model_data(db, "m1") is True  # VACUUM 异常被吞,不阻塞删除


def test_open_db_rejects_legacy_schema(tmp_path):
    """v3.1 起迁移链退役:v2 旧库结构(ts 列/model_pricing 表)明确拒绝,
    不再静默折叠——给清晰诊断而非半迁移状态。"""
    p = tmp_path / "legacy.db"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE model_requests (id INTEGER PRIMARY KEY, ts REAL)")
    conn.commit()
    conn.close()
    from llm_manager.data.persistence import LegacySchemaError, open_db

    try:
        open_db(p)
        raise AssertionError("should have raised LegacySchemaError")
    except LegacySchemaError as e:
        assert "v3.1" in str(e)


# ---- 新库表结构(计费列并入父表)----


def test_open_db_creates_flat_config_tables(tmp_path):
    """新库直建新结构:无 model_scripts/model_pricing;model_schemes 有 command 列;
    model_defs 有 3 计费列;pricing_tiers FK 指向 model_defs。"""
    db = open_db(tmp_path / "t.db")
    tables = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "model_scripts" not in tables and "model_pricing" not in tables
    sc_cols = {r[1] for r in db.conn.execute("PRAGMA table_info(model_schemes)")}
    assert "command" in sc_cols
    md_cols = {r[1] for r in db.conn.execute("PRAGMA table_info(model_defs)")}
    assert {"pricing_type", "hourly_price", "support_cache"} <= md_cols
    fks = {row[2] for row in db.conn.execute("PRAGMA foreign_key_list(pricing_tiers)")}
    assert fks == {"model_defs"}


def test_open_db_creates_cloud_tables(tmp_path):
    db = open_db(tmp_path / "t.db")
    tables = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("cloud_providers", "cloud_models", "cloud_price_tiers", "cloud_mappings"):
        assert t in tables


def test_open_db_new_db_model_requests_has_source(tmp_path):
    db = open_db(tmp_path / "t.db")
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(model_requests)")}
    assert "source" in cols


def test_open_db_ensures_source_column_on_old_db(tmp_path):
    """全库首个列级前向迁移:旧库缺 source 列 → open_db 后自动补列,旧行 source='local'。"""
    import sqlite3

    p = tmp_path / "old.db"
    conn = sqlite3.connect(p)
    conn.execute(
        "CREATE TABLE model_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, model_id INTEGER NOT NULL, "
        "start_time REAL NOT NULL, end_time REAL NOT NULL, input_tokens INTEGER NOT NULL, "
        "output_tokens INTEGER NOT NULL, cache_n INTEGER NOT NULL, prompt_n INTEGER NOT NULL)"
    )
    conn.commit()
    conn.close()

    db = open_db(p)
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(model_requests)")}
    assert "source" in cols
    db.conn.execute("INSERT INTO models (original_name) VALUES ('m')")
    db.conn.execute(
        "INSERT INTO model_requests (model_id, start_time, end_time, input_tokens, output_tokens, cache_n, prompt_n) "
        "VALUES (1, 1, 2, 3, 4, 0, 3)"
    )
    db.conn.commit()
    assert db.conn.execute("SELECT source FROM model_requests").fetchone()["source"] == "local"


# ---- 计量 source(record_usage 写入 + 聚合过滤)----


def test_record_usage_writes_source(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "ds/x", 1, 2, 5, 5, 0, 5, source="cloud")
    assert db.conn.execute("SELECT source FROM model_requests").fetchone()["source"] == "cloud"


def test_usage_series_source_filter(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", 9, 10, 5, 5, 0, 5)
    record_usage(db, "ds/x", 69, 70, 3, 3, 0, 3, source="cloud")
    all_res = usage_series(db, start_ts=0, end_ts=120, bucket_seconds=60)
    assert all_res.total == [10, 6]
    loc = usage_series(db, start_ts=0, end_ts=120, bucket_seconds=60, source="local")
    assert loc.total == [10, 0]
    clo = usage_series(db, start_ts=0, end_ts=120, bucket_seconds=60, source="cloud")
    assert clo.total == [0, 6]


def test_usage_summary_source_filter(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", 5, 10, 100, 20, 60, 40)
    record_usage(db, "ds/x", 5, 10, 50, 10, 0, 50, source="cloud")
    assert usage_summary(db, start_ts=0, end_ts=100).request_count == 2
    assert usage_summary(db, start_ts=0, end_ts=100, source="cloud").request_count == 1
    assert usage_summary(db, start_ts=0, end_ts=100, source="cloud").input_tokens == 50


def test_usage_by_model_source_field(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", 5, 10, 60, 20, 40, 20)
    record_usage(db, "ds/x", 5, 10, 30, 10, 0, 30, source="cloud")
    rows = usage_by_model(db, start_ts=0, end_ts=100)
    by = {r.model: r for r in rows}
    assert by["m1"].source == "local"
    assert by["ds/x"].source == "cloud"
    rows_cloud = usage_by_model(db, start_ts=0, end_ts=100, source="cloud")
    assert [r.model for r in rows_cloud] == ["ds/x"]
