"""用量聚合(token 汇总/分桶/按模型统计)。"""

import math
import time
from dataclasses import dataclass

from llm_manager.data.metering import hit_rate
from llm_manager.data.persistence import Db


@dataclass(frozen=True, slots=True)
class UsageSeries:
    buckets: list[float]  # bucket-start wall-clock epochs (the time axis)
    models: dict[str, list[float]]  # model → value per bucket (tokens 或 元,0-filled)
    total: list[float]  # value per bucket summed across models


def _clock_offset(bucket_seconds: int) -> int:
    """时钟对齐偏移:本地时区对齐(如日窗口对齐本地零点)。"""
    return (-time.localtime().tm_gmtoff) % bucket_seconds


def _bucket_axis(start_ts: float, end_ts: float, bucket_seconds: int) -> tuple[float, list[float]]:
    """时钟对齐桶轴:(first_bucket_start, [buckets])。空窗/非正桶 → (0.0, []).

    Buckets are **absolute** (clock-aligned to multiples of ``bucket_seconds``), not
    relative to the window start — so a request's bucket is fixed and a sliding live
    window scrolls the chart instead of reshaping it. Alignment to LOCAL boundaries
    (e.g. local midnight for daily) via the TZ offset.
    """
    if end_ts <= start_ts or bucket_seconds <= 0:
        return 0.0, []
    offset = _clock_offset(bucket_seconds)
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

    offset = _clock_offset(bucket_seconds)
    rows = db.conn.execute(
        """SELECT m.original_name AS model,
                  CAST((r.end_time - :offset) / :bucket AS INTEGER) * :bucket + :offset AS bucket,
                  SUM(r.input_tokens + r.output_tokens) AS tokens
           FROM model_requests r JOIN models m ON r.model_id = m.id
           WHERE r.end_time >= :start AND r.end_time < :end
           GROUP BY m.original_name, bucket""",
        {
            "start": start_ts,
            "end": end_ts,
            "bucket": bucket_seconds,
            "offset": offset,
        },
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
    cache_hit: int  # SUM(cache_n)
    cache_miss: int  # SUM(prompt_n)
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
    latency_ms: float  # AVG(end_time - start_time) * 1000


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
        out.append(
            ByModelRow(
                model=r["model"],
                input_tokens=int(r["s_in"]),
                output_tokens=int(r["s_out"]),
                cache_n=cache_hit,
                request_count=int(r["n"]),
                hit_rate=hit_rate(cache_hit, cache_miss),
                share=int(r["s_in"]) / total_in if total_in else 0.0,
                latency_ms=float(r["s_lat"] or 0) * 1000,
            )
        )
    return out
