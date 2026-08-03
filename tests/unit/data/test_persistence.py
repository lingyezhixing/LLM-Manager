"""DB schema/migration + 存储管理(data/persistence.py)与 usage 域聚合
(record_usage / record_runtime / usage_series / usage_cost 等,data/usage.py)的测试;
日志 SQL 存储层测试在 tests/unit/data/test_logs.py(与 src 布局对齐)。"""
import json
import sqlite3
import threading
from pathlib import Path

from llm_manager.data.persistence import (
    delete_model_data,
    open_db,
    orphaned_models,
    storage_stats,
)
from llm_manager.data.usage import (
    record_usage,
    record_runtime_start,
    record_runtime_end,
    resolve_model_id,
    tier_cost,
    usage_cost,
    usage_cost_series,
    usage_by_model,
    usage_series,
    usage_summary,
)


def test_open_db_sets_pragmas_and_creates_schema(tmp_path):
    db = open_db(tmp_path / "t.db")
    mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    tables = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "models" in tables
    assert "model_requests" in tables


def test_record_usage_writes_start_end_tokens(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", start=100.0, end=200.0, input_tokens=5, output_tokens=10, cache_n=1, prompt_n=4)
    row = db.conn.execute("SELECT start_time, end_time, input_tokens FROM model_requests").fetchone()
    assert row["start_time"] == 100.0
    assert row["end_time"] == 200.0
    assert row["input_tokens"] == 5


def test_record_usage_auto_creates_model_round_trips(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "Qwen3-4B", start=1.0, end=2.0, input_tokens=100, output_tokens=50, cache_n=20, prompt_n=80)
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
                record_usage(db, "M", start=0.0, end=0.1, input_tokens=1, output_tokens=1, cache_n=0, prompt_n=1)
        except Exception as e:
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
    # bucket=60, range [0,120) → buckets [0, 60]; the time key is end_time
    record_usage(db, "m1", start=9, end=10, input_tokens=5, output_tokens=5, cache_n=0, prompt_n=5)
    record_usage(db, "m1", start=69, end=70, input_tokens=3, output_tokens=3, cache_n=0, prompt_n=3)
    record_usage(db, "m2", start=19, end=20, input_tokens=2, output_tokens=2, cache_n=0, prompt_n=2)
    result = usage_series(db, start_ts=0, end_ts=120, bucket_seconds=60)
    assert result.buckets == [0, 60]
    assert result.models["m1"] == [10, 6]   # 5+5 in bucket 0, 3+3 in bucket 1
    assert result.models["m2"] == [4, 0]    # 2+2 in bucket 0, none → 0-filled
    assert result.total == [14, 6]


def test_usage_series_empty_range_returns_no_buckets(tmp_path):
    db = open_db(tmp_path / "t.db")
    result = usage_series(db, start_ts=0, end_ts=0, bucket_seconds=60)
    assert result.buckets == []
    assert result.total == []


def test_usage_series_buckets_are_clock_aligned_not_start_relative(tmp_path):
    """Buckets align to the clock (multiples of bucket_seconds), independent of the window
    start — so a sliding window scrolls the chart rather than reshuffling each request."""
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", start=69, end=70, input_tokens=1, output_tokens=1, cache_n=0, prompt_n=0)  # end=70 → absolute bucket 60
    result = usage_series(db, start_ts=10, end_ts=130, bucket_seconds=60)  # unaligned start
    assert result.buckets == [0, 60, 120]            # first = floor(10/60)*60 = 0
    assert result.models["m1"] == [0, 2, 0]          # end=70 → bucket 60 → idx 1


def test_migrate_drops_legacy_ts_column(tmp_path):
    """A Round-2 DB with a ts column gets it dropped on open (Option A folds the timestamp
    back into start_time/end_time, now wall-clock as in legacy)."""
    import sqlite3
    p = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(p))
    conn.executescript(
        "CREATE TABLE models (id INTEGER PRIMARY KEY AUTOINCREMENT, original_name TEXT UNIQUE NOT NULL, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE model_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, model_id INTEGER NOT NULL, "
        "ts REAL NOT NULL DEFAULT 0, start_time REAL NOT NULL, end_time REAL NOT NULL, "
        "input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, cache_n INTEGER NOT NULL, "
        "prompt_n INTEGER NOT NULL, FOREIGN KEY (model_id) REFERENCES models(id));"
        "CREATE INDEX idx_model_requests_ts ON model_requests(ts);"
    )
    conn.commit()
    conn.close()

    db = open_db(p)   # migration drops ts
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(model_requests)")}
    assert "ts" not in cols
    assert "start_time" in cols and "end_time" in cols


def test_usage_summary_aggregates_half_open_range(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", start=5.0, end=10.0, input_tokens=100, output_tokens=20, cache_n=60, prompt_n=40)
    record_usage(db, "m1", start=15.0, end=20.0, input_tokens=50, output_tokens=10, cache_n=0, prompt_n=50)
    record_usage(db, "m2", start=25.0, end=30.0, input_tokens=10, output_tokens=5, cache_n=10, prompt_n=0)
    # half-open [0, 25): includes end=10,20; excludes end=30
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
    record_usage(db, "m1", start=5.0, end=10.0, input_tokens=60, output_tokens=20, cache_n=40, prompt_n=20)   # lat 5s
    record_usage(db, "m1", start=12.0, end=15.0, input_tokens=40, output_tokens=10, cache_n=20, prompt_n=20)  # lat 3s
    record_usage(db, "m2", start=15.0, end=18.0, input_tokens=50, output_tokens=10, cache_n=0, prompt_n=50)   # lat 3s
    rows = usage_by_model(db, start_ts=0.0, end_ts=25.0)
    assert [r.model for r in rows] == ["m1", "m2"]   # ordered by input desc
    assert rows[0].input_tokens == 100
    assert rows[0].request_count == 2
    assert rows[0].cache_n == 60
    assert rows[0].share == 100 / 150
    assert rows[0].hit_rate == 0.6
    assert rows[0].latency_ms == 4000.0              # AVG(5s, 3s) = 4s
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
    for t in ("system_settings", "model_defs", "model_aliases", "model_schemes",
              "pricing_tiers", "log_sessions", "log_lines"):
        assert db.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def test_open_db_creates_model_runtime_table(tmp_path):
    db = open_db(tmp_path / "t.db")
    tables = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "model_runtime" in tables


def test_record_runtime_start_end_round_trip(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_runtime_start(db, "m1", start=100.0)
    record_runtime_end(db, "m1", end=250.0)
    row = db.conn.execute(
        "SELECT start_time, end_time FROM model_runtime r JOIN models m ON r.model_id=m.id "
        "WHERE m.original_name='m1'").fetchone()
    assert row["start_time"] == 100.0
    assert row["end_time"] == 250.0


def test_record_runtime_end_targets_latest_open_session(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_runtime_start(db, "m1", start=100.0)
    record_runtime_start(db, "m1", start=200.0)   # second load (first still open)
    record_runtime_end(db, "m1", end=300.0)        # closes the LATEST open session
    rows = db.conn.execute(
        "SELECT start_time, end_time FROM model_runtime r JOIN models m ON r.model_id=m.id "
        "WHERE m.original_name='m1' ORDER BY start_time").fetchall()
    assert rows[0]["end_time"] is None             # first session still open
    assert rows[1]["end_time"] == 300.0            # second closed


def test_tier_cost_no_cache_matches_and_divides_by_million(tmp_path):
    from llm_manager.config import Pricing, PricingTier
    db = open_db(tmp_path / "t.db")   # unused but keeps style consistent  # noqa: F841
    pricing = Pricing(tiers=(PricingTier(tier_index=1, min_input=0, max_input=32768,
                                         input_price=3.0, output_price=9.0),))
    # 1000 input @ 3/M + 500 output @ 9/M = (3000 + 4500)/1e6
    assert tier_cost(pricing, 1000, 500, 0, 0) == (1000 * 3.0 + 500 * 9.0) / 1_000_000


def test_tier_cost_cache_formula(tmp_path):
    from llm_manager.config import Pricing, PricingTier
    # support_cache 是模型级开关(Pricing 上),缓存价仍在阶梯上
    pricing = Pricing(support_cache=True, tiers=(
        PricingTier(tier_index=1, min_input=0, max_input=None,
                    input_price=3.0, output_price=9.0,
                    cache_write_price=3.75, cache_read_price=0.3),))
    # cache_n*read + prompt_n*(input+write) + output*output, /1e6
    expected = (200 * 0.3 + 800 * (3.0 + 3.75) + 500 * 9.0) / 1_000_000
    assert tier_cost(pricing, 1000, 500, 200, 800) == expected


def test_tier_cost_cache_off_uses_plain_formula(tmp_path):
    """support_cache=False(默认)→ 即使阶梯带缓存价也走无缓存公式。"""
    from llm_manager.config import Pricing, PricingTier
    pricing = Pricing(tiers=(
        PricingTier(tier_index=1, min_input=0, max_input=None,
                    input_price=3.0, output_price=9.0,
                    cache_write_price=3.75, cache_read_price=0.3),))
    expected = (1000 * 3.0 + 500 * 9.0) / 1_000_000   # 无缓存公式,忽略缓存价
    assert tier_cost(pricing, 1000, 500, 200, 800) == expected


def test_tier_cost_no_match_returns_zero(tmp_path):
    from llm_manager.config import Pricing, PricingTier
    pricing = Pricing(tiers=(PricingTier(tier_index=1, min_input=0, max_input=100,
                                         input_price=3.0, output_price=9.0),))
    assert tier_cost(pricing, 9999, 0, 0, 0) == 0.0      # outside the tier window


def test_tier_cost_min_zero_closed_min_nonzero_open(tmp_path):
    from llm_manager.config import Pricing, PricingTier
    pricing = Pricing(tiers=(PricingTier(tier_index=1, min_input=100, max_input=None,
                                         input_price=1.0, output_price=0.0),))
    assert tier_cost(pricing, 100, 0, 0, 0) == 0.0       # min=100 (nonzero) → open → 100 not included
    assert tier_cost(pricing, 101, 0, 0, 0) == 101 * 1.0 / 1_000_000


def _cfg_with(pricing):
    from llm_manager.config import AppConfig, Command, ModelConfig, ProgramConfig, Scheme
    return AppConfig(
        program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
        models={"m1": ModelConfig("m1", ("m1",), "Chat", 1, False,
                                  {"s": Scheme("s", frozenset({"gpu"}), Command(exe="x"), {"gpu": 1})},
                                  pricing=pricing)},
        wol=None, claude_configs={})


def test_usage_cost_tier_model_sums_requests(tmp_path):
    from llm_manager.config import Pricing, PricingTier
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", start=5.0, end=10.0, input_tokens=1000, output_tokens=500, cache_n=0, prompt_n=1000)
    record_usage(db, "m1", start=12.0, end=15.0, input_tokens=2000, output_tokens=0, cache_n=0, prompt_n=2000)
    cfg = _cfg_with(Pricing(tiers=(PricingTier(tier_index=1, input_price=3.0, output_price=9.0),)))
    s = usage_cost(db, cfg, start_ts=0.0, end_ts=100.0)
    expected = ((1000 * 3.0 + 500 * 9.0) + (2000 * 3.0)) / 1_000_000
    assert s.total_cost == expected
    assert s.by_model[0].model == "m1" and s.by_model[0].pricing_type == "tier"


def test_usage_cost_hourly_model_uses_runtime_overlap(tmp_path):
    from llm_manager.config import Pricing
    db = open_db(tmp_path / "t.db")
    record_runtime_start(db, "m1", start=0.0)
    record_runtime_end(db, "m1", end=7200.0)            # 2 hours loaded
    cfg = _cfg_with(Pricing(pricing_type="hourly", hourly_price=10.0))
    s = usage_cost(db, cfg, start_ts=0.0, end_ts=3600.0, now=9999.0)   # window = 1 hour
    assert s.total_cost == 10.0                          # 1h × 10/h
    assert s.by_model[0].pricing_type == "hourly"


def test_usage_cost_open_session_uses_now(tmp_path):
    from llm_manager.config import Pricing
    db = open_db(tmp_path / "t.db")
    record_runtime_start(db, "m1", start=0.0)           # never closed
    cfg = _cfg_with(Pricing(pricing_type="hourly", hourly_price=10.0))
    s = usage_cost(db, cfg, start_ts=0.0, end_ts=3600.0, now=3600.0)   # now caps the session at 1h
    assert s.total_cost == 10.0


def test_usage_cost_free_model_yields_zero_and_is_omitted(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", start=5.0, end=10.0, input_tokens=1000, output_tokens=500, cache_n=0, prompt_n=1000)
    from llm_manager.config import AppConfig, Command, ModelConfig, ProgramConfig, Scheme
    cfg = AppConfig(program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
                    models={"m1": ModelConfig("m1", ("m1",), "Chat", 1, False,
                              {"s": Scheme("s", frozenset({"gpu"}), Command(exe="x"), {"gpu": 1})})},
                    wol=None, claude_configs={})
    s = usage_cost(db, cfg, start_ts=0.0, end_ts=100.0)
    assert s.total_cost == 0.0 and s.by_model == []


def test_usage_cost_series_buckets_tier_cost_by_end_time(tmp_path):
    from llm_manager.config import Pricing, PricingTier
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", start=9, end=10, input_tokens=1000, output_tokens=0, cache_n=0, prompt_n=1000)
    record_usage(db, "m1", start=69, end=70, input_tokens=2000, output_tokens=0, cache_n=0, prompt_n=2000)
    cfg = _cfg_with(Pricing(tiers=(PricingTier(tier_index=1, input_price=3.0, output_price=0.0),)))
    res = usage_cost_series(db, cfg, start_ts=0, end_ts=120, bucket_seconds=60)
    assert res.buckets == [0, 60]
    assert res.models["m1"][0] == 1000 * 3.0 / 1_000_000   # end=10 → bucket 0
    assert res.models["m1"][1] == 2000 * 3.0 / 1_000_000   # end=70 → bucket 60
    assert res.total[0] == res.models["m1"][0]
    assert res.total[1] == res.models["m1"][1]


def test_usage_cost_series_hourly_spreads_across_buckets(tmp_path):
    from llm_manager.config import Pricing
    db = open_db(tmp_path / "t.db")
    record_runtime_start(db, "m1", start=0.0)
    record_runtime_end(db, "m1", end=120.0)               # 2 minutes loaded
    cfg = _cfg_with(Pricing(pricing_type="hourly", hourly_price=3600.0))  # 1 元/s
    res = usage_cost_series(db, cfg, start_ts=0, end_ts=120, bucket_seconds=60, now=9999.0)
    assert res.buckets == [0, 60]
    assert res.total == [60.0, 60.0]                       # 60s each × 1 元/s


def test_usage_cost_series_empty_range_returns_no_buckets(tmp_path):
    from llm_manager.config import Pricing
    db = open_db(tmp_path / "t.db")
    cfg = _cfg_with(Pricing())
    res = usage_cost_series(db, cfg, start_ts=0, end_ts=0, bucket_seconds=60)
    assert res.buckets == [] and res.total == [] and res.models == {}


def test_migrate_moves_support_cache_to_model_pricing(tmp_path):
    """P4 回改迁移:旧库(model_pricing 无 support_cache、pricing_tiers 有)→ 开库后上移到模型级。
    叠加 2026-08-03 代码优化迁移:model_pricing 随后并入 model_defs、pricing_tiers 重建改 FK、
    旧表删除——最终 support_cache 落在 model_defs 上。"""
    import sqlite3
    p = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(p))
    conn.executescript(
        "CREATE TABLE model_defs (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, "
        "mode TEXT NOT NULL, port INTEGER NOT NULL, auto_start INTEGER NOT NULL DEFAULT 0, ord INTEGER NOT NULL DEFAULT 0);"
        "CREATE TABLE model_pricing (model_id INTEGER PRIMARY KEY, pricing_type TEXT NOT NULL DEFAULT 'tier', "
        "hourly_price REAL NOT NULL DEFAULT 0, "
        "FOREIGN KEY (model_id) REFERENCES model_defs(id) ON DELETE CASCADE);"
        "CREATE TABLE pricing_tiers (pricing_id INTEGER NOT NULL, tier_index INTEGER NOT NULL, "
        "min_input INTEGER, max_input INTEGER, min_output INTEGER, max_output INTEGER, "
        "input_price REAL, output_price REAL, support_cache INTEGER NOT NULL DEFAULT 0, "
        "cache_write_price REAL, cache_read_price REAL, "
        "FOREIGN KEY (pricing_id) REFERENCES model_pricing(model_id) ON DELETE CASCADE, "
        "PRIMARY KEY (pricing_id, tier_index));"
    )
    conn.commit()
    conn.close()

    db = open_db(p)   # 迁移:model_pricing 补列 → 并入 model_defs;pricing_tiers 删列重建
    md_cols = {r[1] for r in db.conn.execute("PRAGMA table_info(model_defs)")}
    pt_cols = {r[1] for r in db.conn.execute("PRAGMA table_info(pricing_tiers)")}
    tables = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "support_cache" in md_cols
    assert "support_cache" not in pt_cols
    assert "model_pricing" not in tables and "model_scripts" not in tables


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
    record_runtime_start(db, "m2", 500)
    record_runtime_end(db, "m2", 600)
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


# ---- 代码优化(2026-08-03):model_scripts/model_pricing 并入父表 ----


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


def test_migrate_folds_scripts_and_pricing_into_parents(tmp_path):
    """老库(带数据)→ open_db 迁移:model_schemes.command 取回 scripts 内容;
    model_defs 3 列取回 pricing 内容;pricing_tiers 数据保留且 FK 改指 model_defs;
    两旧表消失;二次 open_db 幂等。"""
    p = tmp_path / "t.db"
    conn = sqlite3.connect(p)
    conn.executescript("""
        CREATE TABLE model_defs (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL,
            mode TEXT NOT NULL, port INTEGER NOT NULL, auto_start INTEGER NOT NULL DEFAULT 0,
            ord INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE model_aliases (model_id INTEGER NOT NULL, alias TEXT NOT NULL, ord INTEGER NOT NULL,
            FOREIGN KEY (model_id) REFERENCES model_defs(id) ON DELETE CASCADE, UNIQUE(alias));
        CREATE TABLE model_schemes (id INTEGER PRIMARY KEY AUTOINCREMENT, model_id INTEGER NOT NULL,
            config_source TEXT NOT NULL, required_devices TEXT NOT NULL DEFAULT '[]',
            memory_mb TEXT NOT NULL DEFAULT '{}', ord INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (model_id) REFERENCES model_defs(id) ON DELETE CASCADE,
            UNIQUE(model_id, config_source));
        CREATE TABLE model_scripts (scheme_id INTEGER PRIMARY KEY, command TEXT NOT NULL,
            FOREIGN KEY (scheme_id) REFERENCES model_schemes(id) ON DELETE CASCADE);
        CREATE TABLE model_pricing (model_id INTEGER PRIMARY KEY, pricing_type TEXT NOT NULL DEFAULT 'tier',
            hourly_price REAL NOT NULL DEFAULT 0, support_cache INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (model_id) REFERENCES model_defs(id) ON DELETE CASCADE);
        CREATE TABLE pricing_tiers (pricing_id INTEGER NOT NULL, tier_index INTEGER NOT NULL,
            min_input INTEGER, max_input INTEGER, min_output INTEGER, max_output INTEGER,
            input_price REAL, output_price REAL, cache_write_price REAL, cache_read_price REAL,
            FOREIGN KEY (pricing_id) REFERENCES model_pricing(model_id) ON DELETE CASCADE,
            PRIMARY KEY (pricing_id, tier_index));
        INSERT INTO model_defs (name, mode, port) VALUES ('M', 'Chat', 1);
        INSERT INTO model_schemes (model_id, config_source) VALUES (1, 'S');
        INSERT INTO model_scripts (scheme_id, command) VALUES (1, '{"exe": "q.bat"}');
        INSERT INTO model_pricing (model_id, pricing_type, hourly_price, support_cache) VALUES (1, 'hourly', 2.5, 1);
        INSERT INTO pricing_tiers (pricing_id, tier_index, input_price, output_price) VALUES (1, 1, 3.0, 9.0);
    """)
    conn.commit()
    conn.close()

    db = open_db(p)   # 迁移
    tables = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "model_scripts" not in tables and "model_pricing" not in tables
    sc = db.conn.execute("SELECT command FROM model_schemes").fetchone()
    assert json.loads(sc["command"]) == {"exe": "q.bat"}
    md = db.conn.execute("SELECT pricing_type, hourly_price, support_cache FROM model_defs").fetchone()
    assert (md["pricing_type"], md["hourly_price"], md["support_cache"]) == ("hourly", 2.5, 1)
    t = db.conn.execute("SELECT pricing_id, tier_index, input_price FROM pricing_tiers").fetchone()
    assert (t["pricing_id"], t["tier_index"], t["input_price"]) == (1, 1, 3.0)
    fks = {row[2] for row in db.conn.execute("PRAGMA foreign_key_list(pricing_tiers)")}
    assert fks == {"model_defs"}
    db2 = open_db(p)   # 幂等:二次打开不抛、结构不变
    assert "model_scripts" not in {r[0] for r in db2.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_migrate_new_fk_cascades_from_model_defs(tmp_path):
    """重建后的 pricing_tiers 随 model_defs 删除级联(新 FK 生效)。"""
    db = open_db(tmp_path / "t.db")
    with db.write_lock:
        cur = db.conn.execute("INSERT INTO model_defs (name, mode, port) VALUES ('M', 'Chat', 1)")
        db.conn.execute("INSERT INTO pricing_tiers (pricing_id, tier_index) VALUES (?, 1)", (cur.lastrowid,))
        db.conn.commit()
    with db.write_lock:
        db.conn.execute("DELETE FROM model_defs WHERE name='M'")
        db.conn.commit()
    assert db.conn.execute("SELECT COUNT(*) FROM pricing_tiers").fetchone()[0] == 0
