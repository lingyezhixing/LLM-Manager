from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class BillingMode(Enum):
    TIERED = "tiered"
    HOURLY = "hourly"


@dataclass
class TierPricing:
    tier_index: int
    min_input_tokens: int
    max_input_tokens: int
    min_output_tokens: int
    max_output_tokens: int
    input_price: float
    output_price: float
    support_cache: bool
    cache_write_price: float
    cache_read_price: float


@dataclass
class ModelBilling:
    use_tier_pricing: bool
    hourly_price: float
    tier_pricing: list[TierPricing] = field(default_factory=list)
