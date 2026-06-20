"""Metering sink + token parser contracts."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from llm_manager.domain.meter import TokenUsage
from llm_manager.domain.records import RequestRecord
from llm_manager.registry import Registry


@runtime_checkable
class TokenParser(Protocol):
    def __call__(self, body: bytes) -> TokenUsage: ...


@runtime_checkable
class MeteringSink(Protocol):
    def record_usage(self, record: RequestRecord) -> None: ...


# The path-keyed token parser registry (spec §8). Populated by @token_parser(...)
# in metering/parsers.py (Plan 2). parse_tokens dispatch reads this.
token_parsers: Registry[str, TokenParser] = Registry()
