from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from llm_manager.config.models import ProgramConfig

logger = logging.getLogger(__name__)


class DatabaseEngine:
    def __init__(self, config: ProgramConfig, db_path: Path | None = None):
        if db_path is None:
            db_path = Path("llm_manager.db")
        self._db_path = db_path
        self._engine: Engine | None = None
        self._config = config

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            raise RuntimeError("Database engine not initialized. Call on_start() first.")
        return self._engine

    async def on_start(self) -> None:
        url = f"sqlite:///{self._db_path}"
        self._engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self._enable_wal_mode()
        self._run_migrations()
        logger.info("Database engine started: %s", self._db_path)

    async def on_stop(self) -> None:
        if self._engine:
            self._engine.dispose()
            self._engine = None
            logger.info("Database engine stopped")

    def _enable_wal_mode(self) -> None:
        @sa_event.listens_for(self._engine, "connect")
        def set_wal_mode(dbapi_connection, _):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    def _run_migrations(self) -> None:
        from llm_manager.database.schema import metadata
        metadata.create_all(self._engine)

    def get_connection(self):
        return self._engine.connect()
