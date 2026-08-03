"""model_requests persistence: open_db (PRAGMAs + schema), record_usage (wall-clock
start/end), resolve_model_id, lock-serialized concurrency, fetch_usage, usage_series
(bucketed by end_time), and the legacy ``ts`` migration. Consolidated here to mirror the
src layout (src/llm_manager/data/persistence.py)."""
import json
import sqlite3
import threading
from pathlib import Path

from llm_manager.data import persistence as _p
from llm_manager.data.persistence import (
    delete_model_data,
    fetch_usage,
    open_db,
    orphaned_models,
    record_usage,
    record_runtime_start,
    record_runtime_end,
    resolve_model_id,
    storage_stats,
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


def test_record_usage_auto_creates_model_and_fetch_round_trips(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "Qwen3-4B", start=1.0, end=2.0, input_tokens=100, output_tokens=50, cache_n=20, prompt_n=80)
    rows = fetch_usage(db, "Qwen3-4B", 0.0, 5.0)
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
    assert len(fetch_usage(db, "M", 0.0, 5.0)) == 80


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


# ---- log_sessions / log_lines ----


def test_log_session_crud(tmp_path):
    db = open_db(tmp_path / "t.db")
    sid = _p.log_start_session(db, "system", None, None, 1000.0)
    sid2 = _p.log_start_session(db, "model", "m1", "m1-alias", 2000.0)
    rows = _p.log_sessions(db)
    assert [r["id"] for r in rows] == [sid2, sid]  # 倒序
    assert rows[0]["type"] == "model" and rows[0]["alias"] == "m1-alias"
    _p.log_end_session(db, sid, 1500.0)
    rows = _p.log_sessions(db, type_="system")
    assert rows[0]["end_time"] == 1500.0 and rows[0]["status"] == "ended"


def test_log_lines_insert_and_query(tmp_path):
    db = open_db(tmp_path / "t.db")
    sid = _p.log_start_session(db, "system", None, None, 1000.0)
    ids = _p.log_insert_lines(db, sid, [
        (1, 1000.1, "sys", "info", "boot line"),
        (2, 1000.2, "sys", "warn", "warning"),
        (3, 1000.3, "sys", "error", "boom"),
    ])
    assert len(ids) == 3 and ids[0] < ids[1] < ids[2]
    bf = _p.log_lines_backfill(db, sid, limit=2)
    assert [r["text"] for r in bf] == ["warning", "boom"]
    page = _p.log_lines_before(db, sid, before_id=ids[2], limit=1)
    assert [r["id"] for r in page] == [ids[1]]
    errs = _p.log_lines_backfill(db, sid, limit=10, level="error")
    assert [r["text"] for r in errs] == ["boom"]


def test_log_session_line_count(tmp_path):
    db = open_db(tmp_path / "t.db")
    sid = _p.log_start_session(db, "system", None, None, 1000.0)
    _p.log_insert_lines(db, sid, [(1, 1000.1, "sys", "info", "a")])
    _p.log_insert_lines(db, sid, [(2, 1000.2, "sys", "info", "b")])
    rows = _p.log_sessions(db)
    assert rows[0]["line_count"] == 2


def test_log_insert_lines_empty_returns_empty(tmp_path):
    db = open_db(tmp_path / "t.db")
    assert _p.log_insert_lines(db, 123, []) == []  # 空列表守卫,不触发任何写


def test_log_insert_lines_chunks_large_batches(tmp_path):
    db = open_db(tmp_path / "t.db")
    sid = _p.log_start_session(db, "system", None, None, 1000.0)
    rows = [(i, 1000.0 + i, "sys", "info", f"line {i}") for i in range(1, 400)]  # 399 行 → 3 块(150+150+99)
    ids = _p.log_insert_lines(db, sid, rows)
    assert len(ids) == 399
    assert ids == sorted(ids)  # 分块后仍全局自增、保持插入序
    back = _p.log_lines_backfill(db, sid, limit=5000)
    assert [r["text"] for r in back] == [f"line {i}" for i in range(1, 400)]


def test_log_sessions_model_filter_and_before_pagination(tmp_path):
    db = open_db(tmp_path / "t.db")
    s1 = _p.log_start_session(db, "model", "m1", "m1a", 1000.0)
    s2 = _p.log_start_session(db, "model", "m2", "m2a", 2000.0)
    s3 = _p.log_start_session(db, "system", None, None, 3000.0)
    rows = _p.log_sessions(db, model_name="m1")
    assert [r["id"] for r in rows] == [s1]
    rows = _p.log_sessions(db, limit=2)
    assert [r["id"] for r in rows] == [s3, s2]
    rows = _p.log_sessions(db, limit=2, before_id=s3)
    assert [r["id"] for r in rows] == [s2, s1]


def test_log_search_matches_across_sessions_and_filters(tmp_path):
    db = open_db(tmp_path / "t.db")
    s1 = _p.log_start_session(db, "system", None, None, 1000.0)
    s2 = _p.log_start_session(db, "model", "m1", "m1-alias", 2000.0)
    _p.log_insert_lines(db, s1, [(1, 1000.1, "sys", "info", "boot Error")])
    _p.log_insert_lines(db, s2, [(1, 2000.1, "stdout", "warn", "model startup error")])
    rows = _p.log_search(db, "error")
    assert [r["text"] for r in rows] == ["boot Error", "model startup error"]  # 跨会话 + ASCII 大小写不敏感
    assert rows[0]["session_type"] == "system" and rows[1]["session_type"] == "model"
    rows = _p.log_search(db, "error", session_id=s1)
    assert [r["text"] for r in rows] == ["boot Error"]
    rows = _p.log_search(db, "error", type_="model")
    assert [r["text"] for r in rows] == ["model startup error"]


def test_log_insert_lines_rolls_back_partial_chunks_on_failure(tmp_path):
    """分块插入任一块失败(重复 seq → IntegrityError)→ 整体回滚,不留部分行;
    同连接后续无关 commit 也不得把残留行带落盘。"""
    import pytest
    import sqlite3
    p = tmp_path / "t.db"
    db = open_db(p)
    sid = _p.log_start_session(db, "system", None, None, 1000.0)
    rows = [(i, 1000.0 + i, "sys", "info", f"line {i}") for i in range(1, 201)]  # seq 1..200
    rows.append((151, 3000.0, "sys", "info", "dup seq 151"))                     # 201 行 → 第 2 块内重复
    with pytest.raises(sqlite3.IntegrityError):
        _p.log_insert_lines(db, sid, rows)
    _p.log_end_session(db, sid, 5000.0)  # 同连接后续 commit —— 若残留事务会误提交部分行
    db.conn.close()
    db2 = open_db(p)  # 全新连接读盘,验证零泄漏
    assert db2.conn.execute("SELECT COUNT(*) FROM log_lines").fetchone()[0] == 0
    assert db2.conn.execute("SELECT COUNT(*) FROM log_sessions").fetchone()[0] == 1
    assert db2.conn.execute("SELECT end_time FROM log_sessions").fetchone()[0] == 5000.0


def test_log_cleanup_time_and_count(tmp_path):
    """时间规则:now=200000,days=2 → cutoff 27200;全部早于 cutoff → 清光。
    3 个会话:旧系统会话(1000s,3 行)、旧模型会话(1005s,2 行)、新系统会话(5000s,1 行)。"""
    db = open_db(tmp_path / "t.db")
    old_sys = _p.log_start_session(db, "system", None, None, 1000.0)
    _p.log_insert_lines(db, old_sys, [(1, 1000.1, "sys", "info", "a"), (2, 1000.2, "sys", "info", "b"),
                                      (3, 1000.3, "sys", "info", "c")])
    old_mod = _p.log_start_session(db, "model", "m1", "m1", 1005.0)
    _p.log_insert_lines(db, old_mod, [(1, 1005.1, "out", "info", "d"), (2, 1005.2, "out", "info", "e")])
    new_sys = _p.log_start_session(db, "system", None, None, 5000.0)
    _p.log_insert_lines(db, new_sys, [(1, 5000.1, "sys", "info", "f")])

    removed_s, removed_l = _p.log_cleanup(db, days=2, count=10, now=200000.0)
    assert removed_s == 3 and removed_l == 6
    assert _p.log_sessions(db) == []
    assert _p.log_lines_backfill(db, old_sys, limit=10) == []


def test_log_cleanup_count_keeps_newest(tmp_path):
    db = open_db(tmp_path / "t.db")
    for i in range(3):
        sid = _p.log_start_session(db, "system", None, None, float(1000 + i))
        _p.log_insert_lines(db, sid, [(1, float(1000 + i) + 0.1, "sys", "info", f"l{i}")])
    removed_s, removed_l = _p.log_cleanup(db, days=9999, count=2, now=10000.0)
    assert removed_s == 1 and removed_l == 1           # 最旧 1 会话(1 行)
    rows = _p.log_sessions(db)
    assert [r["start_time"] for r in rows] == [1002.0, 1001.0]


def test_log_cleanup_both_rules_independent(tmp_path):
    db = open_db(tmp_path / "t.db")
    sid1 = _p.log_start_session(db, "system", None, None, 100.0)   # 超期 且 最旧
    _p.log_insert_lines(db, sid1, [(1, 100.1, "sys", "info", "a")])
    sid2 = _p.log_start_session(db, "system", None, None, 5000.0)  # 不超期
    _p.log_insert_lines(db, sid2, [(1, 5000.1, "sys", "info", "b")])
    removed_s, removed_l = _p.log_cleanup(db, days=1, count=10, now=90000.0)  # 仅时间规则触发
    assert removed_s == 1 and removed_l == 1
    assert [r["id"] for r in _p.log_sessions(db)] == [sid2]


def test_log_cleanup_both_rules_fire_simultaneously(tmp_path):
    """两规则同时触发:时间规则删 {100, 200}(cutoff=3600),条数规则(3>2)补最旧 1 会话(100,
    已含)→ 并集去重 → 删 {100, 200}。"""
    db = open_db(tmp_path / "t.db")
    sid1 = _p.log_start_session(db, "system", None, None, 100.0)   # 超期,且最旧
    _p.log_insert_lines(db, sid1, [(1, 100.1, "sys", "info", "a")])
    sid2 = _p.log_start_session(db, "system", None, None, 200.0)   # 超期
    _p.log_insert_lines(db, sid2, [(1, 200.1, "sys", "info", "b")])
    sid3 = _p.log_start_session(db, "system", None, None, 5000.0)  # 新鲜
    _p.log_insert_lines(db, sid3, [(1, 5000.1, "sys", "info", "c")])
    removed_s, removed_l = _p.log_cleanup(db, days=1, count=2, now=90000.0)
    assert removed_s == 2 and removed_l == 2
    rows = _p.log_sessions(db)
    assert [r["id"] for r in rows] == [sid3]
    assert [r["start_time"] for r in rows] == [5000.0]


def test_log_cleanup_no_doomed_returns_zero(tmp_path):
    """无到期会话且条数未超 → 早退 (0, 0),数据原样。"""
    db = open_db(tmp_path / "t.db")
    sid = _p.log_start_session(db, "system", None, None, 1000.0)
    _p.log_insert_lines(db, sid, [(1, 1000.1, "sys", "info", "a")])
    assert _p.log_cleanup(db, days=9999, count=10, now=10000.0) == (0, 0)
    rows = _p.log_sessions(db)
    assert [r["id"] for r in rows] == [sid]
    assert rows[0]["line_count"] == 1


def test_log_cleanup_chunks_large_doomed_sets(tmp_path):
    """IN 子句按 150 分块:把 SQLITE_LIMIT_VARIABLE_NUMBER 降到 999(模拟 stock CPython
    的编译默认;conda 构建默认 250000 会掩盖该问题)→ >999 会话在册时不触发
    too many SQL variables;行/会话数跨块累计精确。"""
    import sqlite3
    db = open_db(tmp_path / "t.db")
    db.conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
    sids = [_p.log_start_session(db, "system", None, None, float(1000 + i)) for i in range(1000)]
    for sid in sids[:200]:  # 200 会话带行 → 行删除跨 2 块
        _p.log_insert_lines(db, sid, [(1, 1001.0, "sys", "info", "x")])
    removed_s, removed_l = _p.log_cleanup(db, days=2, count=10000, now=200000.0)
    assert removed_s == 1000 and removed_l == 200
    assert _p.log_sessions(db) == []


def test_log_close_open_system_sessions(tmp_path):
    """崩溃/强杀残留的进行中 system 会话(end_time NULL)一次性收口,返回收口数。"""
    db = open_db(tmp_path / "t.db")
    resid = _p.log_start_session(db, "system", None, None, 1000.0)
    mid = _p.log_start_session(db, "model", "m1", "m1", 2000.0)   # 非 system,不收口
    n = _p.log_close_open_system_sessions(db, end=5000.0)
    assert n == 1
    rows = _p.log_sessions(db)
    by_id = {r["id"]: r for r in rows}
    assert by_id[resid]["end_time"] == 5000.0
    assert by_id[mid]["end_time"] is None                          # model 会话不受影响
    assert _p.log_close_open_system_sessions(db, end=6000.0) == 0  # 幂等:无残留可收


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
