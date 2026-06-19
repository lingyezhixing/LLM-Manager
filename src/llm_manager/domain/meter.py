"""Token accounting tuple emitted by every parser."""

from __future__ import annotations

from typing import NamedTuple


class TokenUsage(NamedTuple):
    """Canonical metering result.

    cache_n = cache-read (hit) tokens; prompt_n = prompt total incl. cache-write.
    """

    input_tokens: int
    output_tokens: int
    cache_n: int
    prompt_n: int

    def is_zero(self) -> bool:
        return not (self.input_tokens or self.output_tokens or self.cache_n or self.prompt_n)
