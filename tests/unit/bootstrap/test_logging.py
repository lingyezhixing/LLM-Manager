import logging

from llm_manager.bootstrap import logging as applogging


def test_get_logger_works_before_setup():
    root = logging.getLogger()
    saved = list(root.handlers)
    root.handlers.clear()
    try:
        log = applogging.get_logger("preinit")
        assert isinstance(log, logging.Logger)
        log.info("pre-init visible")  # must not raise
    finally:
        root.handlers = saved


def test_setup_logging_sets_level_and_is_idempotent():
    applogging.setup_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG
    applogging.setup_logging("WARNING")  # second call only updates level
    assert logging.getLogger().level == logging.WARNING
