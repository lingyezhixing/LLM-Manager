# Phase 1: 数据库层迁移

## 目标

将 V2 的完整数据库 schema 和数据访问逻辑迁移到 V3 的 `database/` 层，建立支持计费、token 追踪、运行记录的完整数据基础设施。

## 前置条件

- V3 脚手架已搭建（`database/engine.py`, `schema.py`, `repos/` 已存在）
- 无其他层级依赖，可独立测试

## V2 → V3 差异分析

### Schema 差异

| 维度 | V2 (core/data_manager.py) | V3 (database/) |
|------|--------------------------|----------------|
| 连接模式 | 线程本地存储，每线程独立连接 | SQLAlchemy Engine + StaticPool |
| 模型引用 | `model_id` 外键 + `models` 元数据表 | 直接用 `model_name` 字符串 |
| token 字段 | `input_tokens`, `output_tokens`, `cache_n`, `prompt_n` | `prompt_tokens`, `completion_tokens`, `total_tokens` |
| 计费表 | `tier_pricing`, `hourly_pricing`, `billing_methods` 三张表 | `billing_configs` 一张表(JSON存储) |
| 外键 | 完整外键约束 + 级联删除 | 无外键 |

### 核心决策：采用 V2 的 model_id 外键模式

**理由**：
1. V2 的外键+级联删除设计在数据一致性上优于 V3 的字符串引用
2. 计费系统的 tier_pricing / hourly_pricing 依赖 model_id
3. 数据清理（`DELETE FROM models WHERE ...` 一条语句级联清理所有关联数据）

## 迁移内容

### 1.1 升级 Schema（database/schema.py）

**新增表**：

```python
# 模型元数据表（V2 核心表，其他表通过 model_id 关联）
models = Table(
    "models", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("original_name", String(128), nullable=False, unique=True),
    Column("created_at", Float, nullable=False),  # time.time()
)

# 替换 V3 的 model_runtimes，增加 model_id 外键
model_runtime = Table(
    "model_runtime", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("model_id", Integer, nullable=False, index=True),
    Column("start_time", Float, nullable=False),
    Column("end_time", Float, nullable=True),
    # 外键通过应用层维护（SQLAlchemy Core 不做 FK 约束，避免 WAL 模式下性能问题）
)

# 替换 V3 的 request_logs，增加 cache_n/prompt_n 字段
model_requests = Table(
    "model_requests", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("model_id", Integer, nullable=False, index=True),
    Column("start_time", Float, nullable=False),
    Column("end_time", Float, nullable=False),
    Column("input_tokens", Integer, nullable=False, default=0),
    Column("output_tokens", Integer, nullable=False, default=0),
    Column("cache_n", Integer, nullable=False, default=0),
    Column("prompt_n", Integer, nullable=False, default=0),
)

# 程序运行时间（保留 V3 的 program_runtimes，字段一致）

# 计费配置表（V2 的三表设计）
billing_methods = Table(
    "billing_methods", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("model_id", Integer, nullable=False, unique=True),
    Column("use_tier_pricing", Integer, nullable=False, default=1),  # 1=阶梯计费, 0=按时计费
)

hourly_pricing = Table(
    "hourly_pricing", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("model_id", Integer, nullable=False, unique=True),
    Column("hourly_price", Float, nullable=False, default=0.0),
)

tier_pricing = Table(
    "tier_pricing", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("model_id", Integer, nullable=False),
    Column("tier_index", Integer, nullable=False),
    Column("min_input_tokens", Integer, nullable=False),
    Column("max_input_tokens", Integer, nullable=False),
    Column("min_output_tokens", Integer, nullable=False),
    Column("max_output_tokens", Integer, nullable=False),
    Column("input_price", Float, nullable=False),
    Column("output_price", Float, nullable=False),
    Column("support_cache", Integer, nullable=False, default=0),
    Column("cache_write_price", Float, nullable=False, default=0.0),
    Column("cache_read_price", Float, nullable=False, default=0.0),
    # UNIQUE(model_id, tier_index) 通过应用层维护
)
```

**删除 V3 旧表**：`model_runtimes`, `request_logs`, `billing_configs`（由新表替代）

**保留不变**：`program_runtimes`

### 1.2 升级 Repositories

**修改文件**：

| 文件 | 变更 |
|------|------|
| `repos/base.py` | 增加 `_execute_return_id()` 返回 lastrowid；增加 `_get_model_id()` 辅助方法 |
| `repos/model_repo.py` | 重写为基于 model_id 的 ModelRuntimeRepository；新增 ProgramRuntimeRepository |
| `repos/request_repo.py` | 重写为基于 model_id 的 RequestRepository，增加 cache_n/prompt_n 字段 |
| `repos/billing_repo.py` | 重写为三表操作的 BillingRepository（计费方式、阶梯价格、按时价格） |

**BaseRepository 改造**（`repos/base.py`）：

```python
class BaseRepository(Generic[T]):
    def __init__(self, engine: DatabaseEngine):
        self._engine = engine

    # 保留现有方法
    def _execute(self, stmt) -> Any: ...
    def _query(self, stmt) -> list[dict]: ...
    def _query_one(self, stmt) -> dict | None: ...

    # 新增：返回 lastrowid
    def _execute_return_id(self, stmt) -> int:
        with self._engine.engine.connect() as conn:
            result = conn.execute(stmt)
            conn.commit()
            return result.lastrowid

    # 新增：获取或创建 model_id
    def _get_or_create_model_id(self, model_name: str) -> int:
        row = self._query_one(
            select(models).where(models.c.original_name == model_name)
        )
        if row:
            return row["id"]
        return self._execute_return_id(
            models.insert().values(original_name=model_name, created_at=time.time())
        )
```

**ModelRuntimeRepository**（替代 V2 的 model_runtime 操作）：

| 方法 | 对应 V2 方法 |
|------|-------------|
| `record_start(model_name, start_time) -> int` | `add_model_runtime_start()` |
| `record_end(model_name, end_time)` | `update_model_runtime_end()` |
| `get_runtime_in_range(model_name, start, end) -> list[dict]` | `get_model_runtime_in_range()` |

**RequestRepository**（替代 V2 的 model_requests 操作）：

| 方法 | 对应 V2 方法 |
|------|-------------|
| `save_request(model_name, start, end, input_t, output_t, cache_n, prompt_n)` | `add_model_request()` |
| `get_requests(model_name, start, end, buffer) -> list[dict]` | `get_model_requests()` |

**BillingRepository**（替代 V2 的三张计费表操作）：

| 方法 | 对应 V2 方法 |
|------|-------------|
| `get_billing_config(model_name) -> ModelBilling` | `get_model_billing()` |
| `upsert_tier_pricing(model_name, ...)` | `upsert_tier_pricing()` |
| `delete_tier(model_name, tier_index)` | `delete_and_reindex_tier()` |
| `update_billing_method(model_name, use_tier)` | `update_billing_method()` |
| `update_hourly_price(model_name, price)` | `update_hourly_price()` |

### 1.3 删除旧表对应的 V3 schemas

`schemas/billing.py` 当前只保留了 `BillingMode`，这符合此阶段的清理。但需要在 `schemas/` 中新增计费相关的 dataclass：

```python
# schemas/billing.py 新增
@dataclass
class TierPricing:
    tier_index: int
    min_input_tokens: int
    max_input_tokens: int
    min_output_tokens: int
    max_output_tokens: int
    input_price: float
    output_price: float
    support_cache: bool
    cache_write_price: float
    cache_read_price: float

@dataclass
class ModelBilling:
    use_tier_pricing: bool
    hourly_price: float
    tier_pricing: list[TierPricing]
```

### 1.4 DatabaseEngine 改造

`database/engine.py` 的 `_run_migrations()` 需要从 `metadata.create_all()` 改为支持增量迁移：

```python
def _run_migrations(self) -> None:
    from llm_manager.database.schema import metadata
    metadata.create_all(self._engine)
    self._seed_default_billing()
```

新增 `_seed_default_billing(config: AppConfig)` 方法：根据配置文件中的模型列表，为每个模型创建默认计费配置（与 V2 的 `_initialize_database` 中最后一部分逻辑一致）。

**注意**：`DatabaseEngine` 需要接收 `AppConfig` 来知道有哪些模型。当前构造函数接收 `ProgramConfig`，需要改为也接收完整的 `AppConfig`，或者让 `_seed_default_billing` 在 Application 启动时由外部调用。

**推荐方案**：在 `app.py` 的 `_async_run()` 中，`start_all()` 之后额外调用 `db_engine.initialize_billing(config)`。这样 DatabaseEngine 不需要改构造函数。

## 测试方案

### 测试文件：`tests/test_database_layer.py`

```python
"""Phase 1 数据库层测试"""
import pytest
import tempfile
from pathlib import Path

from llm_manager.database.engine import DatabaseEngine
from llm_manager.database.repos.model_repo import ModelRuntimeRepository, ProgramRuntimeRepository
from llm_manager.database.repos.request_repo import RequestRepository
from llm_manager.database.repos.billing_repo import BillingRepository
from llm_manager.config.models import ProgramConfig


class TestSchemaCreation:
    """验证所有表的创建"""

    def setup_method(self):
        self.db = DatabaseEngine(ProgramConfig(), db_path=Path(tempfile.mktemp(suffix=".db")))
        # 同步调用 on_start（测试环境）
        import asyncio
        asyncio.get_event_loop().run_until_complete(self.db.on_start())

    def test_all_tables_created(self):
        """验证 7 张表全部创建"""
        with self.db.engine.connect() as conn:
            from sqlalchemy import inspect
            inspector = inspect(self.db.engine)
            tables = set(inspector.get_table_names())
        expected = {
            "models", "program_runtimes", "model_runtime",
            "model_requests", "billing_methods", "hourly_pricing", "tier_pricing"
        }
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"


class TestModelRuntimeRepository:
    """验证模型运行记录 CRUD"""

    def test_record_start_returns_id(self):
        repo = ModelRuntimeRepository(self.db)
        rid = repo.record_start("test-model", 1000.0)
        assert isinstance(rid, int)
        assert rid > 0

    def test_record_end_updates_latest(self):
        repo = ModelRuntimeRepository(self.db)
        rid = repo.record_start("test-model", 1000.0)
        repo.record_end("test-model", 2000.0)
        rows = repo.get_runtime_in_range("test-model", 0, 3000.0)
        assert len(rows) == 1
        assert rows[0]["end_time"] == 2000.0

    def test_get_or_create_model_id(self):
        """同一模型名应返回相同 id"""
        id1 = self.db._get_or_create_model_id("model-a")  # 如果移到 repo
        id2 = self.db._get_or_create_model_id("model-a")
        assert id1 == id2


class TestRequestRepository:
    """验证请求记录 + token 字段"""

    def test_save_request_with_cache_tokens(self):
        repo = RequestRepository(self.db)
        repo.save_request(
            "test-model", 1000.0, 1001.0,
            input_tokens=100, output_tokens=50,
            cache_n=80, prompt_n=20
        )
        rows = repo.get_requests("test-model", 0, 2000.0)
        assert len(rows) == 1
        assert rows[0]["cache_n"] == 80
        assert rows[0]["prompt_n"] == 20


class TestBillingRepository:
    """验证计费配置 CRUD"""

    def test_default_billing_is_tiered(self):
        repo = BillingRepository(self.db)
        # 需要先 seed
        billing = repo.get_billing_config("test-model")
        assert billing is not None
        assert billing.use_tier_pricing is True

    def test_upsert_tier_pricing(self):
        repo = BillingRepository(self.db)
        repo.upsert_tier_pricing("test-model", tier_index=1, ...)
        billing = repo.get_billing_config("test-model")
        assert len(billing.tier_pricing) >= 1

    def test_switch_to_hourly(self):
        repo = BillingRepository(self.db)
        repo.update_billing_method("test-model", use_tier_pricing=False)
        repo.update_hourly_price("test-model", 10.0)
        billing = repo.get_billing_config("test-model")
        assert billing.use_tier_pricing is False
        assert billing.hourly_price == 10.0


class TestProgramRuntimeRepository:
    """验证程序运行记录"""

    def test_record_and_update(self):
        repo = ProgramRuntimeRepository(self.db)
        rid = repo.record_start(1000.0)
        repo.update_end(rid, 2000.0)
        # 验证更新成功
```

### 测试通过标准

1. `TestSchemaCreation` — 7 张表全部创建，无遗漏
2. `TestModelRuntimeRepository` — 运行记录的 CRUD 完整，model_id 自动创建
3. `TestRequestRepository` — token 四字段（input/output/cache_n/prompt_n）正确存储和查询
4. `TestBillingRepository` — 阶梯计费和按时计费两种模式可切换，tier 的 upsert/delete/reindex 正确
5. `TestProgramRuntimeRepository` — 程序运行记录 CRUD

全部测试通过后，Phase 1 完成，进入 Phase 2。
