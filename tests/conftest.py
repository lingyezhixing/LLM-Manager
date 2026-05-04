"""Phase 1 数据库层测试 fixtures"""
import asyncio
from pathlib import Path

import pytest

from llm_manager.config.models import ProgramConfig
from llm_manager.database.engine import DatabaseEngine


def _run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


@pytest.fixture
def db():
    """创建内存数据库，启动引擎，yield 后清理"""
    engine = DatabaseEngine(ProgramConfig(), db_path=Path(":memory:"))
    _run_async(engine.on_start())
    yield engine
    _run_async(engine.on_stop())
