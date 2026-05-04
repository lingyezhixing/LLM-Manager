from __future__ import annotations

import json

from sqlalchemy import select

from llm_manager.database.engine import DatabaseEngine
from llm_manager.database.schema import billing_configs
from llm_manager.database.repos.base import BaseRepository


class BillingRepository(BaseRepository[dict]):
    def __init__(self, engine: DatabaseEngine):
        super().__init__(engine)

    def save_billing_config(self, model_name: str, mode: str, config: dict) -> None:
        existing = self._query_one(
            select(billing_configs).where(billing_configs.c.model_name == model_name)
        )
        if existing:
            self._execute(
                billing_configs.update()
                .where(billing_configs.c.model_name == model_name)
                .values(mode=mode, config_json=json.dumps(config))
            )
        else:
            self._execute(
                billing_configs.insert().values(
                    model_name=model_name,
                    mode=mode,
                    config_json=json.dumps(config),
                )
            )

    def get_billing_config(self, model_name: str) -> dict | None:
        row = self._query_one(
            select(billing_configs).where(billing_configs.c.model_name == model_name)
        )
        if row:
            return {
                "model_name": row["model_name"],
                "mode": row["mode"],
                "config": json.loads(row["config_json"]),
            }
        return None

    def get_all_billing_configs(self) -> list[dict]:
        rows = self._query(select(billing_configs))
        return [
            {
                "model_name": row["model_name"],
                "mode": row["mode"],
                "config": json.loads(row["config_json"]),
            }
            for row in rows
        ]
