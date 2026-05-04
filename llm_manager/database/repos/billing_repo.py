from __future__ import annotations

from sqlalchemy import and_, select

from llm_manager.database.engine import DatabaseEngine
from llm_manager.database.schema import (
    billing_methods,
    hourly_pricing,
    models,
    tier_pricing,
)
from llm_manager.database.repos.base import BaseRepository
from llm_manager.schemas.billing import ModelBilling, TierPricing


class BillingRepository(BaseRepository[dict]):
    def __init__(self, engine: DatabaseEngine):
        super().__init__(engine)

    def seed_default_billing(self, model_name: str) -> None:
        model_id = self._get_or_create_model_id(model_name)

        existing = self._query_one(
            select(billing_methods).where(billing_methods.c.model_id == model_id)
        )
        if existing:
            return

        self._execute(
            billing_methods.insert().values(
                model_id=model_id, use_tier_pricing=True
            )
        )
        self._execute(
            hourly_pricing.insert().values(
                model_id=model_id, hourly_price=0.0
            )
        )
        self._execute(
            tier_pricing.insert().values(
                model_id=model_id,
                tier_index=1,
                min_input_tokens=0,
                max_input_tokens=32768,
                min_output_tokens=0,
                max_output_tokens=32768,
                input_price=0.0,
                output_price=0.0,
                support_cache=False,
                cache_write_price=0.0,
                cache_read_price=0.0,
            )
        )

    def get_billing_config(self, model_name: str) -> ModelBilling | None:
        row = self._query_one(
            select(models).where(models.c.original_name == model_name)
        )
        if not row:
            return None
        model_id = row["id"]

        method = self._query_one(
            select(billing_methods).where(billing_methods.c.model_id == model_id)
        )
        if not method:
            return None

        hourly_row = self._query_one(
            select(hourly_pricing).where(hourly_pricing.c.model_id == model_id)
        )
        hourly_price = hourly_row["hourly_price"] if hourly_row else 0.0

        tier_rows = self._query(
            select(tier_pricing)
            .where(tier_pricing.c.model_id == model_id)
            .order_by(tier_pricing.c.tier_index.asc())
        )
        tiers = [
            TierPricing(
                tier_index=r["tier_index"],
                min_input_tokens=r["min_input_tokens"],
                max_input_tokens=r["max_input_tokens"],
                min_output_tokens=r["min_output_tokens"],
                max_output_tokens=r["max_output_tokens"],
                input_price=r["input_price"],
                output_price=r["output_price"],
                support_cache=bool(r["support_cache"]),
                cache_write_price=r["cache_write_price"],
                cache_read_price=r["cache_read_price"],
            )
            for r in tier_rows
        ]

        return ModelBilling(
            use_tier_pricing=bool(method["use_tier_pricing"]),
            hourly_price=hourly_price,
            tier_pricing=tiers,
        )

    def upsert_tier_pricing(
        self,
        model_name: str,
        tier_index: int,
        min_input: int,
        max_input: int,
        min_output: int,
        max_output: int,
        input_price: float,
        output_price: float,
        support_cache: bool,
        cache_write_price: float,
        cache_read_price: float,
    ) -> None:
        model_id = self._get_or_create_model_id(model_name)

        existing = self._query_one(
            select(tier_pricing).where(
                and_(
                    tier_pricing.c.model_id == model_id,
                    tier_pricing.c.tier_index == tier_index,
                )
            )
        )
        values = dict(
            min_input_tokens=min_input,
            max_input_tokens=max_input,
            min_output_tokens=min_output,
            max_output_tokens=max_output,
            input_price=input_price,
            output_price=output_price,
            support_cache=support_cache,
            cache_write_price=cache_write_price,
            cache_read_price=cache_read_price,
        )
        if existing:
            self._execute(
                tier_pricing.update()
                .where(
                    and_(
                        tier_pricing.c.model_id == model_id,
                        tier_pricing.c.tier_index == tier_index,
                    )
                )
                .values(**values)
            )
        else:
            self._execute(
                tier_pricing.insert().values(
                    model_id=model_id, tier_index=tier_index, **values
                )
            )

    def delete_tier(self, model_name: str, tier_index: int) -> None:
        row = self._query_one(
            select(models).where(models.c.original_name == model_name)
        )
        if not row:
            return
        model_id = row["id"]

        self._execute(
            tier_pricing.delete().where(
                and_(
                    tier_pricing.c.model_id == model_id,
                    tier_pricing.c.tier_index == tier_index,
                )
            )
        )

        remaining = self._query(
            select(tier_pricing)
            .where(tier_pricing.c.model_id == model_id)
            .order_by(tier_pricing.c.tier_index.asc())
        )
        for new_idx, r in enumerate(remaining, start=1):
            old_idx = r["tier_index"]
            if new_idx != old_idx:
                self._execute(
                    tier_pricing.update()
                    .where(
                        and_(
                            tier_pricing.c.model_id == model_id,
                            tier_pricing.c.tier_index == old_idx,
                        )
                    )
                    .values(tier_index=new_idx)
                )

    def update_billing_method(self, model_name: str, use_tier_pricing: bool) -> None:
        model_id = self._get_or_create_model_id(model_name)

        existing = self._query_one(
            select(billing_methods).where(billing_methods.c.model_id == model_id)
        )
        if existing:
            self._execute(
                billing_methods.update()
                .where(billing_methods.c.model_id == model_id)
                .values(use_tier_pricing=use_tier_pricing)
            )
        else:
            self._execute(
                billing_methods.insert().values(
                    model_id=model_id, use_tier_pricing=use_tier_pricing
                )
            )

    def update_hourly_price(self, model_name: str, hourly_price: float) -> None:
        model_id = self._get_or_create_model_id(model_name)

        existing = self._query_one(
            select(hourly_pricing).where(hourly_pricing.c.model_id == model_id)
        )
        if existing:
            self._execute(
                hourly_pricing.update()
                .where(hourly_pricing.c.model_id == model_id)
                .values(hourly_price=hourly_price)
            )
        else:
            self._execute(
                hourly_pricing.insert().values(
                    model_id=model_id, hourly_price=hourly_price
                )
            )
