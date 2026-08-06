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
    status: str  # "running" | "ended"
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
        return LogLineResponse(id=r.id, ts=r.ts, stream=r.stream, level=r.level, text=r.text)
    return LogLineResponse(
        id=r["id"], ts=r["ts"], stream=r["stream"], level=r["level"], text=r["text"]
    )


def _to_session(r) -> LogSessionResponse:
    """SQL 层的 status 由内存 live_session_ids 计算(运行中=直播会话,end_time 只管时间,
    心跳会把它推到 now)——响应必须透传该字段,不能再按 end_time 判运行中(7279319 解耦
    语义;否则心跳一写 end_time,日志页「运行中」就消失)。"""
    status = r["status"]
    return LogSessionResponse(
        id=r["id"],
        type=r["type"],
        model_name=r["model_name"],
        alias=r["alias"],
        start_time=r["start_time"],
        end_time=r["end_time"],
        status=status,
        duration_s=(r["end_time"] - r["start_time"]) if status == "ended" else None,
        line_count=r["line_count"],
    )
