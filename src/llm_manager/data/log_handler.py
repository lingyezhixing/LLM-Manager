"""System log handler + root-logger setup(控制台 + 时间戳文件 + 清理)。app.py 组合根只做接线。

SystemLogHandler forwards logging records into the logs capture queue without
blocking the caller. Dropped records never affect the main program (the collector
is O(1) append; batching/persistence happen in the data/logs.py flush task).
To be wired by app.py lifespan (install/remove); tests and non-lifespan paths stay clean.
"""

from __future__ import annotations

import datetime
import logging
import sys
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


def _cleanup_old_logs(log_dir: str, keep: int = 10) -> None:
    """保留最近 keep 个 llm-manager_*.log(按 mtime),删旧的。"""
    files = sorted(
        Path(log_dir).glob("llm-manager_*.log"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for stale in files[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass


def setup_logging(level: str = "INFO", log_dir: str = "logs") -> None:
    """配置 root logger(可重配):控制台 + 每次启动一个时间戳文件(留 10 个)。
    每次启动 = 新文件 logs/llm-manager_{ts}.log(非按天轮换,避免长期堆一个文件)。"""
    numeric = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(numeric)
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(numeric)
    console.setFormatter(fmt)
    root.addHandler(console)
    try:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        log_file = (
            Path(log_dir) / f"llm-manager_{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.log"  # noqa: DTZ005 — 文件名时间戳,本地时间即可
        )
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(numeric)
        fh.setFormatter(fmt)
        root.addHandler(fh)
        _cleanup_old_logs(log_dir, keep=10)
        logger.info("logging to %s", log_file)
    except OSError:
        pass
    logging.getLogger("httpx").setLevel(logging.WARNING)  # 降噪:每请求一行太吵,REQ/RESP 已覆盖


class SystemLogHandler(logging.Handler):
    """Synchronous handler → collector callable (``logs.capture_system``).
    Collector must be non-blocking (in-memory append)."""

    def __init__(self, collector: Callable[[str, float, str], None]) -> None:
        super().__init__()
        self._collector = collector

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._collector(record.getMessage(), record.created, record.levelname)
        except Exception:  # noqa: BLE001, S110
            pass  # 日志管道永不影响主程序
