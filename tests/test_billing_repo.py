"""Phase 1 — BillingRepository 测试"""
from llm_manager.database.repos.billing_repo import BillingRepository
from llm_manager.schemas.billing import ModelBilling


class TestSeedDefaultBilling:
    def test_creates_default_tier_pricing(self, db):
        repo = BillingRepository(db)
        repo.seed_default_billing("test-model")
        billing = repo.get_billing_config("test-model")
        assert billing is not None
        assert billing.use_tier_pricing is True
        assert len(billing.tier_pricing) == 1
        assert billing.tier_pricing[0].tier_index == 1

    def test_seed_is_idempotent(self, db):
        repo = BillingRepository(db)
        repo.seed_default_billing("test-model")
        repo.seed_default_billing("test-model")
        billing = repo.get_billing_config("test-model")
        assert len(billing.tier_pricing) == 1


class TestGetBillingConfig:
    def test_returns_model_billing(self, db):
        repo = BillingRepository(db)
        repo.seed_default_billing("test-model")
        billing = repo.get_billing_config("test-model")
        assert isinstance(billing, ModelBilling)
        assert billing.use_tier_pricing is True
        assert billing.hourly_price == 0.0

    def test_nonexistent_returns_none(self, db):
        repo = BillingRepository(db)
        billing = repo.get_billing_config("nonexistent")
        assert billing is None


class TestSwitchToHourly:
    def test_switch_to_hourly_mode(self, db):
        repo = BillingRepository(db)
        repo.seed_default_billing("test-model")
        repo.update_billing_method("test-model", use_tier_pricing=False)
        repo.update_hourly_price("test-model", 10.0)
        billing = repo.get_billing_config("test-model")
        assert billing.use_tier_pricing is False
        assert billing.hourly_price == 10.0


class TestUpsertTierPricing:
    def test_insert_new_tier(self, db):
        repo = BillingRepository(db)
        repo.seed_default_billing("test-model")
        repo.upsert_tier_pricing(
            "test-model", tier_index=2,
            min_input=32768, max_input=131072,
            min_output=32768, max_output=131072,
            input_price=0.5, output_price=1.0,
            support_cache=False, cache_write_price=0.0, cache_read_price=0.0,
        )
        billing = repo.get_billing_config("test-model")
        assert len(billing.tier_pricing) == 2

    def test_update_existing_tier(self, db):
        repo = BillingRepository(db)
        repo.seed_default_billing("test-model")
        repo.upsert_tier_pricing(
            "test-model", tier_index=1,
            min_input=0, max_input=65536,
            min_output=0, max_output=65536,
            input_price=1.0, output_price=2.0,
            support_cache=True, cache_write_price=0.3, cache_read_price=0.1,
        )
        billing = repo.get_billing_config("test-model")
        assert len(billing.tier_pricing) == 1
        tier = billing.tier_pricing[0]
        assert tier.input_price == 1.0
        assert tier.support_cache is True
        assert tier.cache_write_price == 0.3


class TestDeleteTier:
    def test_delete_and_reindex(self, db):
        repo = BillingRepository(db)
        repo.seed_default_billing("test-model")
        repo.upsert_tier_pricing(
            "test-model", tier_index=2,
            min_input=32768, max_input=131072,
            min_output=32768, max_output=131072,
            input_price=0.5, output_price=1.0,
            support_cache=False, cache_write_price=0.0, cache_read_price=0.0,
        )
        repo.upsert_tier_pricing(
            "test-model", tier_index=3,
            min_input=131072, max_input=-1,
            min_output=131072, max_output=-1,
            input_price=0.3, output_price=0.6,
            support_cache=False, cache_write_price=0.0, cache_read_price=0.0,
        )
        assert len(repo.get_billing_config("test-model").tier_pricing) == 3

        repo.delete_tier("test-model", tier_index=2)
        billing = repo.get_billing_config("test-model")
        assert len(billing.tier_pricing) == 2
        assert billing.tier_pricing[0].tier_index == 1
        assert billing.tier_pricing[1].tier_index == 2
