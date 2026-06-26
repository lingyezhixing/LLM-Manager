"""In-memory session usage aggregate (since process start). Reset only on restart.

Fed by the proxy's ``_record_usage`` path (same token parse as the persisted rows, see
``data/metering``). Exposed via ``GET /api/usage/session`` for the 概览 session-stats card.
Module-level singleton (like ``state.py``) — asyncio single-thread → increments need no lock.

``started_at`` is the process-start wall-clock epoch (captured once at import); the
frontend fetches it and ticks uptime locally rather than the backend computing a duration.
Metering semantics (all parsers): ``cache_tokens`` = hit, ``prompt_tokens`` = miss,
``input_tokens`` = cache + prompt → hit_rate = cache_hit / (cache_hit + cache_miss).
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SessionTotals:
    started_at: float        # process start (wall-clock epoch seconds)
    input_tokens: int
    output_tokens: int
    cache_hit: int
    cache_miss: int
    hit_rate: float


@dataclass(slots=True)
class _Counters:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_tokens: int = 0    # hits
    prompt_tokens: int = 0   # misses


_STARTED_AT: float = time.time()   # module import ≈ gateway boot
_c: _Counters = _Counters()


def _reset() -> None:
    """Test helper: clear counters (production resets only via process restart)."""
    global _c
    _c = _Counters()


def add(input_tokens: int, output_tokens: int, cache_tokens: int, prompt_tokens: int) -> None:
    _c.input_tokens += input_tokens
    _c.output_tokens += output_tokens
    _c.cache_tokens += cache_tokens
    _c.prompt_tokens += prompt_tokens


def snapshot() -> SessionTotals:
    hit = _c.cache_tokens
    miss = _c.prompt_tokens
    denom = hit + miss
    return SessionTotals(
        started_at=_STARTED_AT,
        input_tokens=_c.input_tokens,
        output_tokens=_c.output_tokens,
        cache_hit=hit,
        cache_miss=miss,
        hit_rate=hit / denom if denom else 0.0,
    )
