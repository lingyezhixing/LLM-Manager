import logging
from llm_manager.data import log_handler
from llm_manager.data import logs


def _make_collector(delivered):
    def collect(text, ts, levelname):
        delivered.append((text, ts, levelname))

    return collect


def test_handler_delivers_record():
    delivered = []
    h = log_handler.SystemLogHandler(_make_collector(delivered))
    rec = logging.LogRecord("t", logging.WARNING, "f", 1, "disk nearly full", None, None)
    h.emit(rec)
    assert len(delivered) == 1
    text, ts, levelname = delivered[0]
    assert text == "disk nearly full"
    assert levelname == "WARNING"
    assert ts == rec.created  # 透传 record.created


def test_handler_quiet_on_collector_failure():
    def boom(text, ts, levelname):
        raise RuntimeError("collector broke")

    h = log_handler.SystemLogHandler(boom)
    h.emit(logging.LogRecord("t", logging.ERROR, "f", 1, "x", None, None))  # 不抛


def test_handler_levels_pass_through():
    delivered = []
    h = log_handler.SystemLogHandler(_make_collector(delivered))
    for lv in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        h.emit(logging.LogRecord("t", getattr(logging, lv), "f", 1, f"msg {lv}", None, None))
    assert [d[2] for d in delivered] == ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def test_handler_lazy_formats_record():
    delivered = []
    h = log_handler.SystemLogHandler(_make_collector(delivered))
    rec = logging.LogRecord("t", logging.WARNING, "f", 1, "disk %s full", ("nearly",), None)
    h.emit(rec)
    assert delivered[0][0] == "disk nearly full"  # getMessage() 惰性格式化


def test_handler_accepts_capture_system_direct():
    # capture_system 无系统会话时静默丢弃 → 冒烟验证签名兼容、不抛
    h = log_handler.SystemLogHandler(logs.capture_system)
    h.emit(logging.LogRecord("t", logging.INFO, "f", 1, "smoke", None, None))
