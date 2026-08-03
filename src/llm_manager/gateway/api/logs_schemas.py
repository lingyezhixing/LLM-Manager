"""Log API 共享响应 schema 与转换(仅 /api/logs/* 使用)。

- LogLineResponse / LogSessionResponse / LogSearchResponse(typed LogSearchMatch matches)
- sqlite3.Row 或 _logs.LogLine → 响应模型转换 _to_line / _to_session

级别过滤由端点签名 Literal 校验(非法值 422),不再有 _level_param 静默丢弃。
"""
from __future__ import annotations

from pydantic import BaseModel

from llm_manager.data import logs as _logs


class LogLineResponse(BaseModel):
    id: int
    ts: float
    stream: str
    level: str
    text: str


class LogSessionResponse(BaseModel):
    id: int
    type: str
    model_name: str | None
    alias: str | None
    start_time: float
    end_time: float | None
    status: str           # "running" | "ended"
    duration_s: float | None
    line_count: int


class LogSearchMatch(BaseModel):
    session_id: int
    line: LogLineResponse


class LogSearchResponse(BaseModel):
    total: int
    matches: list[LogSearchMatch]


def _to_line(r) -> LogLineResponse:
    """sqlite3.Row(回填/检索)或 _logs.LogLine(广播实时行) → 统一响应模型。"""
    if isinstance(r, _logs.LogLine):
        return LogLineResponse(id=r.id, ts=r.ts, stream=r.stream,
                               level=r.level, text=r.text)
    return LogLineResponse(id=r["id"], ts=r["ts"], stream=r["stream"],
                           level=r["level"], text=r["text"])


def _to_session(r) -> LogSessionResponse:
    ended = r["end_time"] is not None
    return LogSessionResponse(
        id=r["id"], type=r["type"], model_name=r["model_name"], alias=r["alias"],
        start_time=r["start_time"], end_time=r["end_time"],
        status="ended" if ended else "running",
        duration_s=(r["end_time"] - r["start_time"]) if ended else None,
        line_count=r["line_count"],
    )
