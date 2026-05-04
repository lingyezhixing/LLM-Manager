from sqlalchemy import Boolean, Column, Float, Integer, MetaData, String, Table

metadata = MetaData()

models = Table(
    "models", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("original_name", String(128), nullable=False, unique=True),
    Column("created_at", Float, nullable=False),
)

program_runtimes = Table(
    "program_runtimes", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("start_time", Float, nullable=False),
    Column("end_time", Float, nullable=True),
)

model_runtime = Table(
    "model_runtime", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("model_id", Integer, nullable=False, index=True),
    Column("start_time", Float, nullable=False),
    Column("end_time", Float, nullable=True),
)

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

billing_methods = Table(
    "billing_methods", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("model_id", Integer, nullable=False, unique=True),
    Column("use_tier_pricing", Boolean, nullable=False, default=True),
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
    Column("model_id", Integer, nullable=False, index=True),
    Column("tier_index", Integer, nullable=False),
    Column("min_input_tokens", Integer, nullable=False),
    Column("max_input_tokens", Integer, nullable=False),
    Column("min_output_tokens", Integer, nullable=False),
    Column("max_output_tokens", Integer, nullable=False),
    Column("input_price", Float, nullable=False),
    Column("output_price", Float, nullable=False),
    Column("support_cache", Boolean, nullable=False, default=False),
    Column("cache_write_price", Float, nullable=False, default=0.0),
    Column("cache_read_price", Float, nullable=False, default=0.0),
)
