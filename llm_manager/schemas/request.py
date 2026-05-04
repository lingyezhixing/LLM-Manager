from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_n: int = 0
    prompt_n: int = 0
