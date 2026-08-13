"""Session usage aggregate (since process start): input/output/cache-hit/miss + hit-rate.

Metering semantics (all parsers): cache_tokens = hit, prompt_tokens = miss,
input_tokens = cache + prompt. So hit_rate = cache_hit / (cache_hit + cache_miss)."""

from __future__ import annotations

from llm_manager.data.usage import _reset_counters as _reset
from llm_manager.data.usage import session_add as add
from llm_manager.data.usage import session_snapshot as snapshot


def test_add_accumulates_across_calls() -> None:
    _reset()
    add(100, 50, 30, 70)
    add(200, 10, 150, 50)
    s = snapshot(123.0)
    assert s.input_tokens == 300
    assert s.output_tokens == 60
    assert s.cache_hit == 180  # 30 + 150
    assert s.cache_miss == 120  # 70 + 50
    assert s.hit_rate == 0.6  # 180 / (180 + 120)


def test_hit_rate_zero_when_no_input() -> None:
    _reset()
    assert snapshot(123.0).hit_rate == 0.0


def test_reset_clears_counters() -> None:
    add(1000, 0, 0, 1000)
    _reset()
    s = snapshot(123.0)
    assert s.input_tokens == 0
    assert s.hit_rate == 0.0


def test_snapshot_includes_started_at_epoch() -> None:
    """started_at = caller-provided wall-clock epoch; echoed verbatim across snapshots."""
    _reset()
    s = snapshot(123.0)
    assert isinstance(s.started_at, float)
    assert s.started_at == 123.0
    assert snapshot(123.0).started_at == s.started_at
