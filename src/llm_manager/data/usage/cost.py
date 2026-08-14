"""计费逻辑(compute-on-read,无快照)。

计费口径(设计决策,勿当缺口):**无计价快照**——每次查询用当前配置全量重算
(compute-on-read)。改价/改计费类型会回溯改写全部历史费用、删模型则该模型历史
费用归零:费用=当前架构下的派生值;token 是 DB 事实不受配置影响(两视图语义不同、
各自自洽)。若未来需要"历史账单不可变",再给请求/运行段冻结单价(表加列)。
"""

import math
import time
from dataclasses import dataclass

from llm_manager.config import AppConfig, Pricing, PricingTier
from llm_manager.data.persistence import Db
from llm_manager.data.usage.aggregate import UsageSeries, _bucket_axis


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """两段时间窗口 [a_start,a_end) 与 [b_start,b_end) 的重叠秒数。"""
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _hourly_cost(
    seg_start: float, seg_end: float, hourly_price: float, win_start: float, win_end: float
) -> float:
    """运行段与窗口 [win_start, win_end) 重叠秒数 × 小时单价/3600(usage_cost 与
    usage_cost_series 共用,避免计费语义在汇总/分桶两处漂移)。"""
    return _overlap(win_start, win_end, seg_start, seg_end) * hourly_price / 3600.0


def _tier_cost_row(models, row) -> float:
    """逐请求 tier_cost(usage_cost 与 usage_cost_series 共用)。非 tier/未配置 → 0.0。"""
    mc = models.get(row["model"])
    if mc is None or mc.pricing.pricing_type != "tier":
        return 0.0
    return tier_cost(
        mc.pricing,
        int(row["input_tokens"]),
        int(row["output_tokens"]),
        int(row["cache_n"]),
        int(row["prompt_n"]),
    )


def _tier_matches(t: PricingTier, inp: int, out: int) -> bool:
    """First-tier-wins window match (legacy semantics): min=0 closed, else open;
    max None/negative = unbounded."""
    lo_i = 0 if (t.min_input is None or t.min_input < 0) else t.min_input
    hi_i = math.inf if (t.max_input is None or t.max_input < 0) else t.max_input
    i_ok = (inp >= lo_i) if lo_i == 0 else (inp > lo_i)
    lo_o = 0 if (t.min_output is None or t.min_output < 0) else t.min_output
    hi_o = math.inf if (t.max_output is None or t.max_output < 0) else t.max_output
    o_ok = (out >= lo_o) if lo_o == 0 else (out > lo_o)
    return i_ok and inp <= hi_i and o_ok and out <= hi_o


def tier_cost(pricing: Pricing, input_t: int, output_t: int, cache_n: int, prompt_n: int) -> float:
    """Per-request tier cost in yuan. First matching tier wins; no match → 0.
    Cache formula (legacy): cache_n×read + prompt_n×(input+write) + output×output.
    support_cache 是模型级开关(pricing.support_cache),控制缓存计费是否生效。"""
    if pricing.pricing_type != "tier" or not pricing.tiers:
        return 0.0
    for t in pricing.tiers:
        if not _tier_matches(t, input_t, output_t):
            continue
        if pricing.support_cache:
            raw = (
                cache_n * t.cache_read_price
                + prompt_n * (t.input_price + t.cache_write_price)
                + output_t * t.output_price
            )
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
    cfg: AppConfig,
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
            c = _tier_cost_row(cfg.models, row)
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
            c = _hourly_cost(row["start_time"], sess_end, rate, start_ts, end_ts)
            if c > 0:
                acc[row["model"]] = acc.get(row["model"], 0.0) + c

    ptype = {n: m.pricing.pricing_type for n, m in cfg.models.items()}
    by_model = [
        CostByModel(
            model=n, pricing_type=ptype.get(n, "tier"), cost=round(c, 6)
        )  # 🔵7:与 total 同精度 round(6),显示一致
        for n, c in acc.items()
        if c > 0
    ]
    by_model.sort(key=lambda x: x.cost, reverse=True)
    return CostSummary(total_cost=round(sum(x.cost for x in by_model), 6), by_model=by_model)


def usage_cost_series(
    db: Db,
    cfg: AppConfig,
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
            c = _tier_cost_row(tier_models, row)
            if c <= 0:
                continue
            idx = int((row["end_time"] - first) // bucket_seconds)
            if 0 <= idx < n:
                models.setdefault(row["model"], [0.0] * n)[idx] += c
                total[idx] += c

    # hourly:单次批量查询所有 hourly 模型的运行段,逐桶摊重叠时长(原 O(N) 查询 → 1 次)。
    hourly_rates = {
        n: m.pricing.hourly_price
        for n, m in cfg.models.items()
        if m.pricing.pricing_type == "hourly" and m.pricing.hourly_price > 0
    }
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
                cost = _hourly_cost(
                    row["start_time"], sess_end, rate, b_start, b_start + bucket_seconds
                )
                if cost > 0:
                    models.setdefault(row["model"], [0.0] * n)[i] += cost
                    total[i] += cost

    return UsageSeries(buckets=buckets, models=models, total=total)
