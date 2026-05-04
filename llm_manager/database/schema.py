from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text

metadata = MetaData()

model_runtimes = Table(
    "model_runtimes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("model_name", String(128), nullable=False, index=True),
    Column("start_time", Float, nullable=False),
    Column("end_time", Float, nullable=True),
)

request_logs = Table(
    "request_logs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("request_id", String(64), nullable=False, unique=True),
    Column("model_name", String(128), nullable=False, index=True),
    Column("timestamp", Float, nullable=False),
    Column("prompt_tokens", Integer, default=0),
    Column("completion_tokens", Integer, default=0),
    Column("total_tokens", Integer, default=0),
    Column("latency_ms", Float, default=0.0),
    Column("success", Integer, default=1),
    Column("error_message", Text, nullable=True),
)

program_runtimes = Table(
    "program_runtimes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("start_time", Float, nullable=False),
    Column("end_time", Float, nullable=True),
)

billing_configs = Table(
    "billing_configs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("model_name", String(128), nullable=False, unique=True),
    Column("mode", String(32), nullable=False),
    Column("config_json", Text, nullable=False),
)
