from dataclasses import dataclass


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class RequestRecord:
    id: str
    model_name: str
    timestamp: float
    token_usage: TokenUsage
    latency_ms: float
    success: bool = True
    error_message: str | None = None
