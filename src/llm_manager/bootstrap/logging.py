"""Logging bootstrap. get_logger() works BEFORE setup_logging() (lazy basicConfig
fallback) — every module calls get_logger at import time, so the fallback must stay."""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def setup_logging(log_level: str = "INFO") -> None:
    """Attach a stderr handler once; subsequent calls only update the level."""
    global _CONFIGURED
    root = logging.getLogger()
    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        root.addHandler(handler)
        _CONFIGURED = True
    root.setLevel(log_level)


def get_logger(name: str) -> logging.Logger:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    return logging.getLogger(name)
