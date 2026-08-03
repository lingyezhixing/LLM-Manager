"""Usage-row persistence + cost aggregation over model_requests/model_runtime。schema/迁移与存储管理在 data/persistence.py,日志存储 in data/logs.py。"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from llm_manager.data.metering import hit_rate
from llm_manager.data.persistence import Db

if TYPE_CHECKING:
    from llm_manager.config import AppConfig, Pricing, PricingTier


def _resolve_model_id_locked(db: Db, model_name: str) -> int:
    """Insert-or-return model id. Caller MUST already hold db.write_lock
    (threading.Lock is non-reentrant, so we cannot re-acquire here)."""
    row = db.conn.execute("SELECT id FROM models WHERE original_name = ?", (model_name,)).fetchone()
    if row:
        return row["id"]
    cur = db.conn.execute("INSERT INTO models (original_name) VALUES (?)", (model_name,))
    db.conn.commit()
    assert cur.lastrowid is not None  # AUTOINCREMENT PK always yields an int on INSERT
    return cur.lastrowid


def resolve_model_id(db: Db, model_name: str) -> int:
    """Public entry: takes the lock itself for standalone callers."""
    with db.write_lock:
        return _resolve_model_id_locked(db, model_name)


def record_usage(db: Db, model_name: str, start: float, end: float,
                 input_tokens: int, output_tokens: int, cache_n: int, prompt_n: int) -> None:
    with db.write_lock:
        mid = _resolve_model_id_locked(db, model_name)
        db.conn.execute(
            "INSERT INTO model_requests (model_id, start_time, end_time, input_tokens, output_tokens, cache_n, prompt_n) VALUES (?,?,?,?,?,?,?)",
            (mid, start, end, input_tokens, output_tokens, cache_n, prompt_n),
        )
        db.conn.commit()


def record_runtime_start(db: Db, model_name: str, start: float) -> None:
    """Begin a model-loaded billing session (model reached ROUTING). end_time stays NULL
    until record_runtime_end. Auto-creates the models row (a model can load before any request)."""
    with db.write_lock:
        mid = _resolve_model_id_locked(db, model_name)
        db.conn.execute(
            "INSERT INTO model_runtime (model_id, start_time, end_time) VALUES (?,?,NULL)",
            (mid, start),
        )
        db.conn.commit()


def record_runtime_end(db: Db, model_name: str, end: float) -> None:
    """Close the latest still-open session for the model (cooperative stop or crash)."""
    with db.write_lock:
        mid = _resolve_model_id_locked(db, model_name)
        db.conn.execute(
            "UPDATE model_runtime SET end_time=? WHERE id=("
            "SELECT id FROM model_runtime WHERE model_id=? AND end_time IS NULL ORDER BY id DESC LIMIT 1)",
            (end, mid),
        )
        db.conn.commit()


@dataclass(frozen=True, slots=True)
class UsageSeries:
    buckets: list[float]            # bucket-start wall-clock epochs (the time axis)
    models: dict[str, list[float]]  # model → value per bucket (tokens 或 元,0-filled)
    total: list[float]              # value per bucket summed across models


def _bucket_axis(start_ts: float, end_ts: float, bucket_seconds: int) -> tuple[float, list[float]]:
    """时钟对齐桶轴:(first_bucket_start, [buckets])。空窗/非正桶 → (0.0, []).

    Buckets are **absolute** (clock-aligned to multiples of ``bucket_seconds``), not
    relative to the window start — so a request's bucket is fixed and a sliding live
    window scrolls the chart instead of reshaping it. Alignment to LOCAL boundaries
    (e.g. local midnight for daily) via the TZ offset.
    """
    if end_ts <= start_ts or bucket_seconds <= 0:
        return 0.0, []
    offset = (-time.localtime().tm_gmtoff) % bucket_seconds
    first = float(math.floor((start_ts - offset) / bucket_seconds) * bucket_seconds + offset)
    n = max(1, math.ceil((end_ts - first) / bucket_seconds))
    return first, [first + i * bucket_seconds for i in range(n)]


def usage_series(db: Db, *, start_ts: float, end_ts: float, bucket_seconds: int) -> UsageSeries:
    """Aggregate token consumption (input + output) per model + total, bucketed by wall-clock
    end_time (the request's completion timestamp — when usage is recorded).

    Buckets are **absolute** (clock-aligned to multiples of ``bucket_seconds``), not relative
    to the window start — so a request's bucket is fixed and a sliding live window scrolls
    the chart instead of reshaping it. Returns the full bucket axis 0-filled for continuity.
    ``tokens = input + output``.
    """
    first, buckets = _bucket_axis(start_ts, end_ts, bucket_seconds)
    n = len(buckets)
    if not buckets:
        return UsageSeries(buckets=[], models={}, total=[])

    rows = db.conn.execute(
        """SELECT m.original_name AS model,
                  CAST((r.end_time - :offset) / :bucket AS INTEGER) * :bucket + :offset AS bucket,
                  SUM(r.input_tokens + r.output_tokens) AS tokens
           FROM model_requests r JOIN models m ON r.model_id = m.id
           WHERE r.end_time >= :start AND r.end_time < :end
           GROUP BY m.original_name, bucket""",
        {"start": start_ts, "end": end_ts, "bucket": bucket_seconds, "offset": (-time.localtime().tm_gmtoff) % bucket_seconds},
    ).fetchall()

    models: dict[str, list[float]] = {}
    total: list[float] = [0.0] * n
    for row in rows:
        idx = int((row["bucket"] - first) // bucket_seconds)
        if 0 <= idx < n:
            tokens = int(row["tokens"])
            models.setdefault(row["model"], [0.0] * n)[idx] = tokens
            total[idx] += tokens
    return UsageSeries(buckets=buckets, models=models, total=total)


@dataclass(frozen=True, slots=True)
class UsageSummary:
    input_tokens: int
    output_tokens: int
    cache_hit: int       # SUM(cache_n)
    cache_miss: int      # SUM(prompt_n)
    hit_rate: float
    request_count: int


def usage_summary(db: Db, *, start_ts: float, end_ts: float) -> UsageSummary:
    """Aggregate token usage over the half-open window [start_ts, end_ts) by wall-clock
    end_time. Empty window → zeros (hit_rate 0.0)."""
    row = db.conn.execute(
        """SELECT COALESCE(SUM(input_tokens), 0) AS s_in,
                  COALESCE(SUM(output_tokens), 0) AS s_out,
                  COALESCE(SUM(cache_n), 0) AS s_cache,
                  COALESCE(SUM(prompt_n), 0) AS s_miss,
                  COUNT(*) AS n
           FROM model_requests
           WHERE end_time >= ? AND end_time < ?""",
        (start_ts, end_ts),
    ).fetchone()
    cache_hit = int(row["s_cache"])
    cache_miss = int(row["s_miss"])
    return UsageSummary(
        input_tokens=int(row["s_in"]),
        output_tokens=int(row["s_out"]),
        cache_hit=cache_hit,
        cache_miss=cache_miss,
        hit_rate=hit_rate(cache_hit, cache_miss),
        request_count=int(row["n"]),
    )


@dataclass(frozen=True, slots=True)
class ByModelRow:
    model: str
    input_tokens: int
    output_tokens: int
    cache_n: int
    request_count: int
    hit_rate: float
    share: float
    latency_ms: float       # AVG(end_time - start_time) * 1000


def usage_by_model(db: Db, *, start_ts: float, end_ts: float) -> list[ByModelRow]:
    """Per-model aggregates over [start_ts, end_ts), ordered by input_tokens desc.
    share = model input / total input (0.0 when no input). latency_ms = mean wall-clock
    request duration in ms."""
    rows = db.conn.execute(
        """SELECT m.original_name AS model,
                  SUM(r.input_tokens) AS s_in,
                  SUM(r.output_tokens) AS s_out,
                  SUM(r.cache_n) AS s_cache,
                  SUM(r.prompt_n) AS s_miss,
                  COUNT(*) AS n,
                  AVG(r.end_time - r.start_time) AS s_lat
           FROM model_requests r JOIN models m ON r.model_id = m.id
           WHERE r.end_time >= ? AND r.end_time < ?
           GROUP BY m.original_name
           ORDER BY s_in DESC""",
        (start_ts, end_ts),
    ).fetchall()
    total_in = sum(int(r["s_in"]) for r in rows)
    out: list[ByModelRow] = []
    for r in rows:
        cache_hit = int(r["s_cache"])
        cache_miss = int(r["s_miss"])
        out.append(ByModelRow(
            model=r["model"],
            input_tokens=int(r["s_in"]),
            output_tokens=int(r["s_out"]),
            cache_n=cache_hit,
            request_count=int(r["n"]),
            hit_rate=hit_rate(cache_hit, cache_miss),
            share=int(r["s_in"]) / total_in if total_in else 0.0,
            latency_ms=float(r["s_lat"] or 0) * 1000,
        ))
    return out


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """两段时间窗口 [a_start,a_end) 与 [b_start,b_end) 的重叠秒数。"""
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _tier_matches(t: "PricingTier", inp: int, out: int) -> bool:  # type: ignore[name-defined]
    """First-tier-wins window match (legacy semantics): min=0 closed, else open;
    max None/negative = unbounded."""
    lo_i = 0 if (t.min_input is None or t.min_input < 0) else t.min_input
    hi_i = math.inf if (t.max_input is None or t.max_input < 0) else t.max_input
    i_ok = (inp >= lo_i) if lo_i == 0 else (inp > lo_i)
    lo_o = 0 if (t.min_output is None or t.min_output < 0) else t.min_output
    hi_o = math.inf if (t.max_output is None or t.max_output < 0) else t.max_output
    o_ok = (out >= lo_o) if lo_o == 0 else (out > lo_o)
    return i_ok and inp <= hi_i and o_ok and out <= hi_o


def tier_cost(pricing: "Pricing", input_t: int, output_t: int, cache_n: int, prompt_n: int) -> float:  # type: ignore[name-defined]
    """Per-request tier cost in yuan. First matching tier wins; no match → 0.
    Cache formula (legacy): cache_n×read + prompt_n×(input+write) + output×output.
    support_cache 是模型级开关(pricing.support_cache),控制缓存计费是否生效。"""
    if pricing.pricing_type != "tier" or not pricing.tiers:
        return 0.0
    for t in pricing.tiers:
        if not _tier_matches(t, input_t, output_t):
            continue
        if pricing.support_cache:
            raw = cache_n * t.cache_read_price + prompt_n * (t.input_price + t.cache_write_price) + output_t * t.output_price
        else:
            raw = input_t * t.input_price + output_t * t.output_price
        return raw / 1_000_000
    return 0.0


@dataclass(frozen=True, slots=True)
class CostByModel:
    model: str
    pricing_type: str
    cost: float


@dataclass(frozen=True, slots=True)
class CostSummary:
    total_cost: float
    by_model: list[CostByModel]


def usage_cost(
    db: Db,
    cfg: "AppConfig",  # type: ignore[name-defined]
    *,
    start_ts: float,
    end_ts: float,
    now: float | None = None,
) -> CostSummary:
    """Aggregate cost (yuan) over [start_ts, end_ts). tier 模型逐请求 tier_cost;
    hourly 模型按 model_runtime 与窗口重叠秒 × hourly_price/3600。免费/无数据模型省略。

    两套独立数据源:tier 走 model_requests(按 end_time 落窗);hourly 走
    model_runtime(按与窗口的重叠时长)。end_time IS NULL 的运行段用 now 收口。"""
    now_ts = now if now is not None else time.time()
    acc: dict[str, float] = {}

    # tier:逐请求计费,按 end_time 落入窗口
    tier_names = {n for n, m in cfg.models.items() if m.pricing.pricing_type == "tier"}
    if tier_names:
        rows = db.conn.execute(
            "SELECT m.original_name AS model, r.input_tokens, r.output_tokens, r.cache_n, r.prompt_n "
            "FROM model_requests r JOIN models m ON r.model_id=m.id "
            "WHERE r.end_time>=? AND r.end_time<?",
            (start_ts, end_ts),
        ).fetchall()
        for row in rows:
            mc = cfg.models.get(row["model"])
            if mc is None or mc.pricing.pricing_type != "tier":
                continue
            c = tier_cost(
                mc.pricing,
                int(row["input_tokens"]),
                int(row["output_tokens"]),
                int(row["cache_n"]),
                int(row["prompt_n"]),
            )
            if c:
                acc[row["model"]] = acc.get(row["model"], 0.0) + c

    # hourly:运行段与窗口的重叠时长 × 单价/3600
    hourly = {
        n: m.pricing.hourly_price
        for n, m in cfg.models.items()
        if m.pricing.pricing_type == "hourly" and m.pricing.hourly_price > 0
    }
    if hourly:
        rows = db.conn.execute(
            "SELECT m.original_name AS model, r.start_time, r.end_time "
            "FROM model_runtime r JOIN models m ON r.model_id=m.id "
            "WHERE r.start_time < ? AND (r.end_time IS NULL OR r.end_time > ?)",
            (end_ts, start_ts),
        ).fetchall()
        for row in rows:
            rate = hourly.get(row["model"])
            if not rate:
                continue
            sess_end = row["end_time"] if row["end_time"] is not None else now_ts
            overlap = _overlap(start_ts, end_ts, row["start_time"], sess_end)
            if overlap > 0:
                acc[row["model"]] = acc.get(row["model"], 0.0) + overlap * rate / 3600.0

    ptype = {n: m.pricing.pricing_type for n, m in cfg.models.items()}
    by_model = [
        CostByModel(model=n, pricing_type=ptype.get(n, "tier"), cost=c)
        for n, c in acc.items()
        if c > 0
    ]
    by_model.sort(key=lambda x: x.cost, reverse=True)
    return CostSummary(total_cost=round(sum(x.cost for x in by_model), 6), by_model=by_model)


def usage_cost_series(
    db: Db,
    cfg: "AppConfig",  # type: ignore[name-defined]
    *,
    start_ts: float,
    end_ts: float,
    bucket_seconds: int,
    now: float | None = None,
) -> UsageSeries:
    """Bucketed cost series (元/桶),时钟对齐分桶(同 usage_series)。tier 成本按请求
    end_time 落桶;hourly 成本按运行段与各桶的重叠时长摊到桶。返回 UsageSeries 形
    (total/models 的值是元,非 token)。"""
    first, buckets = _bucket_axis(start_ts, end_ts, bucket_seconds)
    n = len(buckets)
    if not buckets:
        return UsageSeries(buckets=[], models={}, total=[])
    now_ts = now if now is not None else time.time()
    models: dict[str, list[float]] = {}
    total = [0.0] * n

    # tier:单次批量查询所有 tier 模型的请求,逐行 tier_cost + 按 end_time 落桶(原 O(N) 查询 → 1 次)。
    # 镜像同文件 usage_cost 的批量模式;original_name 与 cfg 模型键同源,行为不变。
    tier_models = {n: m for n, m in cfg.models.items() if m.pricing.pricing_type == "tier"}
    if tier_models:
        names = list(tier_models)
        placeholders = ",".join("?" * len(names))
        rows = db.conn.execute(
            f"SELECT mm.original_name AS model, r.end_time, r.input_tokens, r.output_tokens, r.cache_n, r.prompt_n "
            f"FROM model_requests r JOIN models mm ON r.model_id=mm.id "
            f"WHERE mm.original_name IN ({placeholders}) AND r.end_time>=? AND r.end_time<?",
            (*names, start_ts, end_ts),
        ).fetchall()
        for row in rows:
            mc = tier_models.get(row["model"])
            if mc is None:
                continue
            c = tier_cost(mc.pricing, int(row["input_tokens"]), int(row["output_tokens"]),
                          int(row["cache_n"]), int(row["prompt_n"]))
            if c <= 0:
                continue
            idx = int((row["end_time"] - first) // bucket_seconds)
            if 0 <= idx < n:
                models.setdefault(row["model"], [0.0] * n)[idx] += c
                total[idx] += c

    # hourly:单次批量查询所有 hourly 模型的运行段,逐桶摊重叠时长(原 O(N) 查询 → 1 次)。
    hourly_rates = {n: m.pricing.hourly_price / 3600.0
                    for n, m in cfg.models.items()
                    if m.pricing.pricing_type == "hourly" and m.pricing.hourly_price > 0}
    if hourly_rates:
        names = list(hourly_rates)
        placeholders = ",".join("?" * len(names))
        rows = db.conn.execute(
            f"SELECT mm.original_name AS model, r.start_time, r.end_time "
            f"FROM model_runtime r JOIN models mm ON r.model_id=mm.id "
            f"WHERE mm.original_name IN ({placeholders}) AND r.start_time < ? AND (r.end_time IS NULL OR r.end_time > ?)",
            (*names, end_ts, start_ts),
        ).fetchall()
        for row in rows:
            rate = hourly_rates.get(row["model"])
            if not rate:
                continue
            sess_end = row["end_time"] if row["end_time"] is not None else now_ts
            for i in range(n):
                b_start = first + i * bucket_seconds
                ov = _overlap(b_start, b_start + bucket_seconds, row["start_time"], sess_end)
                if ov > 0:
                    cost = ov * rate
                    models.setdefault(row["model"], [0.0] * n)[i] += cost
                    total[i] += cost

    return UsageSeries(buckets=buckets, models=models, total=total)
