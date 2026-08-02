"""Log API 共享响应 schema 与转换(旧 /api/models/{alias}/logs* 与新 /api/logs/* 共用)。

单源,避免两组端点重复定义 / 同名不同形态的隐患:
- LogLineResponse / LogSessionResponse / LogSearchResponse(typed LogSearchMatch matches)
- 级别白名单 _LOG_LEVELS + ?level= 解析 _level_param
- sqlite3.Row 或 _logs.LogLine → 响应模型转换 _to_line / _to_session

注意:旧端点 /api/models/{alias}/logs/search 的响应是 matches: list[int](旧前端契约),
与这里的 LogSearchResponse(matches: list[LogSearchMatch]) 形态不同 —— 它用的是
models.py 的 ModelLogSearchResponse,类名不同、形态各一,不共用本模块的 LogSearchResponse。
"""
from __future__ import annotations

from fastapi import Request
from pydantic import BaseModel

from llm_manager.data import logs as _logs

_LOG_LEVELS = ("info", "ok", "warn", "error")


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


def _level_param(request: Request) -> str | None:
    lv = request.query_params.get("level")
    return lv if lv in _LOG_LEVELS else None
