"""Per-model live log capture: 内存会话日志 + 级别推断 + Broadcaster 扇出(SSE 用)。

``capture(alias, line, stream)`` 是 supervisor ``on_output`` 的落点(经 call_soon_threadsafe
回到事件循环)。捕获绑定子进程生命周期:spawn 起读、进程退出 EOF 止;会话日志保留至该模型
下次 spawn(新会话 id 重置)。Phase 1 仅内存(大上限);Phase 2 加持久归档 + 分页/搜索。"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from llm_manager.realtime import Broadcaster

_ERR = re.compile(r"error|fail|exception|traceback", re.I)
_OK = re.compile(r"listening|ready|started|server.*ok", re.I)


def infer_level(text: str, stream: str) -> str:
    if stream == "err" and _ERR.search(text):
        return "error"
    if stream == "err":
        return "warn"
    if _OK.search(text):
        return "ok"
    return "info"


@dataclass(frozen=True, slots=True)
class LogLine:
    id: int
    ts: float            # 墙钟(捕获时刻)
    stream: str          # "out" | "err"
    level: str           # "info" | "ok" | "warn" | "error"
    text: str


@dataclass(frozen=True, slots=True)
class LogSearch:
    matches: list[int]   # 匹配行 id(升序)
    total: int


class SessionLog:
    """单模型本次会话的内存日志。append O(1) 摊销;backfill 返回最近 limit 行;
    before 返回 id < line_id 的最近 limit 行(往前翻页);search 全文检索(可按 level)。"""

    def __init__(self, cap: int = 100_000) -> None:
        self._lines: list[LogLine] = []
        self._next_id = 1
        self._bc: Broadcaster[LogLine] = Broadcaster()
        self._cap = cap

    def append(self, line: str, stream: str) -> LogLine:
        ll = LogLine(self._next_id, time.time(), stream, infer_level(line, stream), line)
        self._next_id += 1
        self._lines.append(ll)
        if len(self._lines) > self._cap:
            self._lines = self._lines[-self._cap:]   # 丢最旧(Phase 2 归档兜底)
        self._bc.publish(ll)
        return ll

    def backfill(self, limit: int, level: str | None = None) -> list[LogLine]:
        sel = self._lines if level is None else [ll for ll in self._lines if ll.level == level]
        return sel[-limit:]

    def before(self, line_id: int, limit: int, level: str | None = None) -> list[LogLine]:
        """id < line_id 的最近 limit 行(升序)——往前翻页 / 搜索跳转时载入历史窗口。"""
        sel = [ll for ll in self._lines if ll.id < line_id and (level is None or ll.level == level)]
        return sel[-limit:]

    def search(self, q: str, level: str | None = None) -> LogSearch:
        """全文子串检索(大小写不敏感),可叠加 level 过滤。返回升序匹配行 id + 总数。"""
        needle = q.lower()
        matches = [ll.id for ll in self._lines
                   if needle in ll.text.lower() and (level is None or ll.level == level)]
        return LogSearch(matches=matches, total=len(matches))

    def subscribe(self): return self._bc.subscribe()
    def unsubscribe(self, q): self._bc.unsubscribe(q)


# ---- 模块级注册表(事件循环单线程,无需锁)----
_sessions: dict[str, SessionLog] = {}


def _get(alias: str) -> SessionLog:
    s = _sessions.get(alias)
    if s is None:
        s = SessionLog()
        _sessions[alias] = s
    return s


def capture(alias: str, line: str, stream: str) -> LogLine:
    return _get(alias).append(line, stream)


def backfill(alias: str, limit: int, level: str | None = None) -> list[LogLine]:
    return _get(alias).backfill(limit, level)


def before(alias: str, before: int, limit: int, level: str | None = None) -> list[LogLine]:
    return _get(alias).before(before, limit, level)


def search(alias: str, q: str, level: str | None = None) -> LogSearch:
    return _get(alias).search(q, level)


def subscribe(alias: str):
    return _get(alias).subscribe()


def unsubscribe(alias: str, q) -> None:
    s = _sessions.get(alias)
    if s is not None:
        s.unsubscribe(q)


def end_session(alias: str) -> None:
    """模型停止/中断:结束本次会话(丢弃内存日志;下次 capture 起新会话,id 从 1 重来)。"""
    _sessions.pop(alias, None)


def reset() -> None:
    """测试隔离。"""
    _sessions.clear()
