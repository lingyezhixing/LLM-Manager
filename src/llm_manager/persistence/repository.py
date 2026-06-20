"""Repository: the persistence implementation of MeteringSink + session writes.

Implements ports.metering.MeteringSink.record_usage (the live-path write) and the
program/model runtime session writes (heartbeat updates latest row's end_time).
"""

from __future__ import annotations

from llm_manager.domain.records import RequestRecord
from llm_manager.persistence.store import SqliteStore
from llm_manager.ports.metering import MeteringSink


class Repository(MeteringSink):
    """Concrete MeteringSink + session recorder backed by SqliteStore."""

    def __init__(self, store: SqliteStore) -> None:
        self.store = store

    # --- MeteringSink --------------------------------------------------------
    def record_usage(self, record: RequestRecord) -> None:
        if record.usage.is_zero():
            return
        model_id = self._model_id(record.model_name)
        self.store.execute(
            "INSERT INTO model_requests "
            "(model_id, start_time, end_time, input_tokens, output_tokens, cache_n, prompt_n) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                model_id,
                record.start_time,
                record.end_time,
                record.usage.input_tokens,
                record.usage.output_tokens,
                record.usage.cache_n,
                record.usage.prompt_n,
            ),
        )

    # --- sessions ------------------------------------------------------------
    def record_program_start(self, start_time: float) -> int:
        cur = self.store.execute(
            "INSERT INTO program_runtime (start_time, end_time) VALUES (?, ?)",
            (start_time, start_time),
        )
        return int(cur.lastrowid or 0)

    def record_program_end(self, end_time: float) -> None:
        self.store.execute(
            "UPDATE program_runtime SET end_time = ? "
            "WHERE id = (SELECT MAX(id) FROM program_runtime)",
            (end_time,),
        )

    def record_model_start(self, model_name: str, start_time: float) -> None:
        model_id = self._model_id(model_name)
        self.store.execute(
            "INSERT INTO model_runtime (model_id, start_time, end_time) VALUES (?, ?, ?)",
            (model_id, start_time, start_time),
        )

    def record_model_end(self, model_name: str, end_time: float) -> None:
        model_id = self._model_id(model_name)
        self.store.execute(
            "UPDATE model_runtime SET end_time = ? "
            "WHERE id = (SELECT MAX(id) FROM model_runtime WHERE model_id = ?)",
            (end_time, model_id),
        )

    # --- helpers -------------------------------------------------------------
    def _model_id(self, name: str) -> int:
        self.store.execute(
            "INSERT OR IGNORE INTO models (original_name) VALUES (?)", (name,)
        )
        row = self.store.execute(
            "SELECT id FROM models WHERE original_name = ?", (name,)
        ).fetchone()
        return int(row["id"])
