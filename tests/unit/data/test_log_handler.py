import logging
from llm_manager.data import log_handler

def test_handler_delivers_record():
    delivered = []
    h = log_handler.SystemLogHandler(delivered.append)
    rec = logging.LogRecord("t", logging.WARNING, "f", 1, "disk nearly full", None, None)
    h.emit(rec)
    assert len(delivered) == 1
    text, ts, levelname = delivered[0]
    assert text == "disk nearly full"
    assert levelname == "WARNING"
    assert ts == rec.created          # 透传 record.created


def test_handler_quiet_on_collector_failure():
    def boom(text, ts, levelname):
        raise RuntimeError("collector broke")
    h = log_handler.SystemLogHandler(boom)
    h.emit(logging.LogRecord("t", logging.ERROR, "f", 1, "x", None, None))  # 不抛


def test_handler_levels_pass_through():
    delivered = []
    h = log_handler.SystemLogHandler(delivered.append)
    for lv in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        h.emit(logging.LogRecord("t", getattr(logging, lv), "f", 1, f"msg {lv}", None, None))
    assert [d[2] for d in delivered] == ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
