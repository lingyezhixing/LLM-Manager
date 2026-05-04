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

7 张表定义（删除旧表 `model_runtimes`, `request_logs`, `billing_configs`）：

| 表名 | 说明 | 关键字段 |
|------|------|---------|
| `models` | 模型元数据 | `id`, `original_name`(unique), `created_at`(float) |
| `program_runtimes` | 程序运行时间 | `id`, `start_time`, `end_time`(nullable) |
| `model_runtime` | 模型运行记录 | `model_id`(index), `start_time`, `end_time`(nullable, NULL=运行中) |
| `model_requests` | 请求记录 | `model_id`(index), `start/end_time`, `input/output_tokens`, `cache_n`, `prompt_n` |
| `billing_methods` | 计费方式 | `model_id`(unique), `use_tier_pricing`(bool) |
| `hourly_pricing` | 按时计费价格 | `model_id`(unique), `hourly_price` |
| `tier_pricing` | 阶梯计费配置 | `model_id`(index), `tier_index`, 12个价格/区间字段 |

**关键设计决策**：
- `model_runtime.end_time` 用 `nullable=True`（NULL=运行中），优于 V2 的 `end_time=start_time`
- 外键通过**应用层维护**，不在 SQLAlchemy 层声明 FK 约束
- `billing_methods.use_tier_pricing` 使用 `Boolean` 类型（比 V2 的 Integer 更语义化）
- `tier_pricing` 的 UNIQUE(model_id, tier_index) 通过应用层维护
- `created_at` 使用 `time.time()` 浮点数

### 1.2 升级 Repositories

**BaseRepository**（`repos/base.py`）增强：

| 新增方法 | 说明 |
|---------|------|
| `_execute_return_id(stmt) -> int` | 执行 INSERT 并返回 lastrowid |
| `_get_or_create_model_id(model_name) -> int` | 获取或创建 model_id，所有 repo 共用 |

**ModelRuntimeRepository**（替代 V2 的 model_runtime 操作）：

| 方法 | 签名 | 对应 V2 方法 |
|------|------|-------------|
| `record_start` | `(model_name, start_time) -> int` | `add_model_runtime_start()` |
| `record_end_by_name` | `(model_name, end_time) -> None` | `update_model_runtime_end()` |
| `record_end_by_id` | `(record_id, end_time) -> None` | Phase 4 需要的按 ID 更新 |
| `get_runtime_in_range` | `(model_name, start, end) -> list[dict]` | `get_model_runtime_in_range()` |

**ProgramRuntimeRepository**（增强 V3 的 ProgramRepository）：

| 方法 | 签名 | 对应 V2 方法 |
|------|------|-------------|
| `record_start` | `(start_time) -> int` | `add_program_runtime_start()` |
| `update_end` | `(record_id, end_time) -> None` | `update_program_runtime_end()` |
| `get_runtime_records` | `(limit=0) -> list[dict]` | `get_program_runtime(limit)` |

**RequestRepository**（重写为 V2 的 4-token 字段模式）：

| 方法 | 签名 | 对应 V2 方法 |
|------|------|-------------|
| `save_request` | `(model_name, start, end, input_t, output_t, cache_n, prompt_n)` | `add_model_request()` |
| `get_requests` | `(model_name, start, end, buffer_seconds=60) -> list[dict]` | `get_model_requests()` |

**BillingRepository**（重写为三表操作）：

| 方法 | 签名 | 对应 V2 方法 |
|------|------|-------------|
| `seed_default_billing` | `(model_name) -> None` | `_initialize_database` 中计费部分 |
| `get_billing_config` | `(model_name) -> ModelBilling \| None` | `get_model_billing()` |
| `upsert_tier_pricing` | `(model_name, tier_index, ...)` | `upsert_tier_pricing()` |
| `delete_tier` | `(model_name, tier_index) -> None` | `delete_and_reindex_tier()` |
| `update_billing_method` | `(model_name, use_tier: bool)` | `update_billing_method()` |
| `update_hourly_price` | `(model_name, price: float)` | `update_hourly_price()` |

### 1.3 Schemas 更新

**schemas/billing.py** 新增 dataclass：
- `TierPricing`（10 个字段，与 V2 的 `core/data_manager.py` 一致）
- `ModelBilling`（use_tier_pricing, hourly_price, tier_pricing list）

**schemas/request.py** 更新 `TokenUsage`：
- 从 `prompt_tokens/completion_tokens/total_tokens` 改为 `input_tokens/output_tokens/cache_n/prompt_n`

### 1.4 DatabaseEngine

`_run_migrations()` 无需修改——它已经使用 `metadata.create_all()`，新 schema 自动生效。

计费默认值的 seed 不在 DatabaseEngine 中进行，改为在 `app.py` 启动时通过 `BillingRepository.seed_default_billing()` 按模型调用。

### 1.5 类名变更

| 旧名 | 新名 | 说明 |
|------|------|------|
| `ModelRepository` | `ModelRuntimeRepository` | 更精确反映职责 |
| `ProgramRepository` | `ProgramRuntimeRepository` | 与 ModelRuntimeRepository 命名对称 |

受影响文件：`repos/__init__.py`, `app.py`

## 测试方案

### 测试文件结构

```
tests/
├── conftest.py              # db fixture (内存数据库)
├── test_schema.py           # Schema 创建验证
├── test_base_repo.py        # _get_or_create_model_id 测试
├── test_model_repo.py       # ModelRuntimeRepository CRUD
├── test_program_repo.py     # ProgramRuntimeRepository CRUD
├── test_request_repo.py     # RequestRepository CRUD
├── test_billing_repo.py     # BillingRepository CRUD + tier reindex
```

### 测试覆盖

| 测试类 | 测试数量 | 覆盖内容 |
|--------|---------|---------|
| `TestSchemaCreation` | 5 | 7 张表创建、列名/类型验证、end_time nullable |
| `TestGetOrCreateModelId` | 4 | 新建、幂等、不同名、快速连续调用 |
| `TestRecordStart` | 2 | 返回 ID、自动创建模型 |
| `TestRecordEndByName` | 2 | 更新 NULL 记录、不存在时无操作 |
| `TestRecordEndById` | 1 | 按 ID 更新 |
| `TestGetRuntimeInRange` | 4 | 范围过滤、运行中记录（NULL end_time）、空结果 |
| `TestProgramRuntime` | 4 | CRUD、limit、无 limit |
| `TestSaveAndGetRequest` | 6 | 4-token 字段、多条记录、buffer、自动创建模型 |
| `TestSeedDefaultBilling` | 2 | 创建默认配置、幂等 |
| `TestGetBillingConfig` | 2 | 返回 ModelBilling、不存在返回 None |
| `TestSwitchToHourly` | 1 | 切换计费模式 |
| `TestUpsertTierPricing` | 2 | 插入新 tier、更新已有 tier |
| `TestDeleteTier` | 1 | 删除 + reindex |

**共 36 个测试**，全部通过。

### 测试通过标准

1. `TestSchemaCreation` — 7 张表全部创建，列定义正确
2. `TestGetOrCreateModelId` — 幂等性、并发安全性
3. `TestRecordStart/End` — 运行记录的 CRUD 完整，NULL end_time 正确处理
4. `TestSaveAndGetRequest` — token 四字段正确存储和查询，buffer 语义正确
5. `TestSeedDefaultBilling` — 默认配置创建正确且幂等
6. `TestUpsertTierPricing` — 新增和更新 tier 正确
7. `TestDeleteTier` — 删除后 reindex 正确

全部测试通过后，Phase 1 完成，进入 Phase 2。
