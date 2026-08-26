"""进程内会话计数器(重启清零,概览 session-stats 卡)。

由 proxy 的 ``_record_usage`` 路径喂入(与落库行的 token 解析相同,见
``data/metering``)。模块级单例(同 ``state.py``)— asyncio 单线程 →
自增无需锁。``started_at`` 为进程启动时刻的 wall-clock epoch,由调用方传入;
前端获取后本地走 uptime 计时。计量语义(所有解析器):``cache_tokens`` = 命中,
``prompt_tokens`` = 未命中,``input_tokens`` = cache + prompt
→ hit_rate = cache_hit / (cache_hit + cache_miss)。

双账本语义:本模块=内存侧(进程生命周期,重启清零,概览 session-stats 卡);
model_requests/model_runtime 落库侧=持久历史(用量/成本页)。两账本口径独立,
内存侧不做落库。
"""

from dataclasses import dataclass

from llm_manager.data.metering import hit_rate


@dataclass(frozen=True, slots=True)
class SessionTotals:
    started_at: float  # 进程启动时刻(wall-clock epoch 秒)
    input_tokens: int
    output_tokens: int
    cache_hit: int
    cache_miss: int
    hit_rate: float


@dataclass(slots=True)
class _Counters:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_tokens: int = 0  # 命中
    prompt_tokens: int = 0  # 未命中


_c: _Counters = _Counters()


def _reset_counters() -> None:
    """测试辅助:清空计数器(生产环境仅经进程重启清零)。"""
    global _c
    _c = _Counters()


def session_add(
    input_tokens: int, output_tokens: int, cache_tokens: int, prompt_tokens: int
) -> None:
    _c.input_tokens += input_tokens
    _c.output_tokens += output_tokens
    _c.cache_tokens += cache_tokens
    _c.prompt_tokens += prompt_tokens


def session_snapshot(started_at: float) -> SessionTotals:
    """started_at 由调用方传入(app 实例级,与 /api/system/info 单源);
    (进程启动时刻的 wall-clock epoch,time.time() 值)。"""
    hit = _c.cache_tokens
    miss = _c.prompt_tokens
    return SessionTotals(
        started_at=started_at,
        input_tokens=_c.input_tokens,
        output_tokens=_c.output_tokens,
        cache_hit=hit,
        cache_miss=miss,
        hit_rate=hit_rate(hit, miss),
    )
