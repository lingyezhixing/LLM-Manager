"""Test-wide fixtures.

create_app() calls setup_logging(), which attaches a TimedRotatingFileHandler to
logs/llm-manager.log on the root logger. Without isolation, every test that
builds the app pollutes the production log file with pytest output (which then
shows up when the real app runs and appends to the same file). Stub setup_logging
for the whole suite so tests never touch the real log file.
"""
import pytest

from llm_manager import app


@pytest.fixture(autouse=True)
def _isolate_logging(monkeypatch):
    monkeypatch.setattr(app, "setup_logging", lambda *a, **k: None)
