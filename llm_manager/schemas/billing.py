from dataclasses import dataclass
from enum import Enum


class BillingMode(Enum):
    TIERED = "tiered"
    HOURLY = "hourly"


@dataclass
class TierConfig:
    name: str
    start: int
    end: int
    price: float


@dataclass
class BillingConfig:
    model_name: str
    mode: BillingMode = BillingMode.TIERED
    tiers: list[TierConfig] | None = None
    hourly_rate: float | None = None


@dataclass
class CostRecord:
    model_name: str
    timestamp: float
    tokens: int
    cost: float
    billing_mode: BillingMode
