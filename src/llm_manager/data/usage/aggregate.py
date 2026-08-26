"""用量聚合(token 汇总/分桶/按模型统计)。"""

import math
import time
from dataclasses import dataclass

from llm_manager.data.metering import hit_rate
from llm_manager.data.persistence import Db


@dataclass(frozen=True, slots=True)
class UsageSeries:
    buckets: list[float]  # 桶起点 wall-clock epoch(时间轴)
    models: dict[str, list[float]]  # 模型 → 每桶数值(tokens 或 元,缺桶补 0)
    total: list[float]  # 每桶跨模型求和


def _clock_offset(bucket_seconds: int) -> int:
    """时钟对齐偏移:本地时区对齐(如日窗口对齐本地零点)。"""
    return (-time.localtime().tm_gmtoff) % bucket_seconds


def _bucket_axis(start_ts: float, end_ts: float, bucket_seconds: int) -> tuple[float, list[float]]:
    """时钟对齐桶轴:(first_bucket_start, [buckets])。空窗/非正桶 → (0.0, []).

    桶是**绝对**的(时钟对齐到 ``bucket_seconds`` 的倍数),而非相对窗口起点——
    故请求的桶固定,滑动 live 窗口滚动图表而不是重塑它。经 TZ 偏移对齐本地边界
    (如日窗口对齐本地零点)。
    """
    if end_ts <= start_ts or bucket_seconds <= 0:
        return 0.0, []
    offset = _clock_offset(bucket_seconds)
    first = float(math.floor((start_ts - offset) / bucket_seconds) * bucket_seconds + offset)
    n = max(1, math.ceil((end_ts - first) / bucket_seconds))
    return first, [first + i * bucket_seconds for i in range(n)]


def usage_series(db: Db, *, start_ts: float, end_ts: float, bucket_seconds: int) -> UsageSeries:
    """按模型 + 总计聚合 token 消耗(input + output),按墙钟 end_time(请求完成
    时刻——用量记录时点)分桶。

    桶是**绝对**的(时钟对齐到 ``bucket_seconds`` 的倍数),而非相对窗口起点——
    故请求的桶固定,滑动 live 窗口滚动图表而不是重塑它。返回完整桶轴,缺桶补 0
    保证连续性。``tokens = input + output``。
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
    """半开窗口 [start_ts, end_ts) 内的 token 用量聚合,按墙钟 end_time 归窗。
    空窗口 → 全零(hit_rate 0.0)。"""
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
    """[start_ts, end_ts) 内的按模型聚合,按 input_tokens 降序。
    share = 模型输入 / 总输入(无输入时 0.0)。latency_ms = 平均墙钟请求时长(ms)。"""
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
