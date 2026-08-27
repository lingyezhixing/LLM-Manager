"""DB 打开/schema/迁移 + 存储统计与孤立模型维护。"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """列级前向迁移:表缺该列则 ADD COLUMN(幂等;旧库守护只拒不迁,新列默认值恒向后兼容)。"""
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


class LegacySchemaError(RuntimeError):
    """v2 旧库结构(model_requests.ts 列 / model_pricing / model_scripts 表);v3.1 起迁移链退役,不再支持。"""


@dataclass(frozen=True, slots=True)
class Db:
    conn: sqlite3.Connection
    write_lock: threading.Lock


def push_end_times(db: Db, table: str, ids: set[int], now: float) -> int:
    """心跳共用:把一批进行中项(log_sessions / model_runtime)的 end_time 推到 now。
    由调用方选好 id 集(内存 live 集),本函数负责建 IN 占位符 + UPDATE + commit。"""
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    with db.write_lock:
        cur = db.conn.execute(
            f"UPDATE {table} SET end_time=? WHERE id IN ({placeholders})", (now, *ids)
        )
        n = cur.rowcount
        db.conn.commit()
        return n


def open_db(path: Path) -> Db:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _migrate(conn)  # 必须在 CREATE IF NOT EXISTS 之前检查旧库 schema
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_name TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS model_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id INTEGER NOT NULL,
            start_time REAL NOT NULL,
            end_time REAL NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            cache_n INTEGER NOT NULL,
            prompt_n INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'local',
            FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_model_requests_model_id ON model_requests(model_id);
        CREATE INDEX IF NOT EXISTS idx_model_requests_end ON model_requests(end_time);
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS model_defs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            mode TEXT NOT NULL,
            port INTEGER NOT NULL,
            auto_start INTEGER NOT NULL DEFAULT 0,
            pricing_type TEXT NOT NULL DEFAULT 'tier',
            hourly_price REAL NOT NULL DEFAULT 0,
            support_cache INTEGER NOT NULL DEFAULT 0,
            ord INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS model_aliases (
            model_id INTEGER NOT NULL,
            alias TEXT NOT NULL,
            ord INTEGER NOT NULL,
            FOREIGN KEY (model_id) REFERENCES model_defs(id) ON DELETE CASCADE,
            UNIQUE(alias)
        );
        CREATE TABLE IF NOT EXISTS model_schemes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id INTEGER NOT NULL,
            config_source TEXT NOT NULL,
            required_devices TEXT NOT NULL DEFAULT '[]',
            memory_mb TEXT NOT NULL DEFAULT '{}',
            command TEXT NOT NULL DEFAULT '{}',
            ord INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (model_id) REFERENCES model_defs(id) ON DELETE CASCADE,
            UNIQUE(model_id, config_source)
        );
        CREATE TABLE IF NOT EXISTS pricing_tiers (
            pricing_id INTEGER NOT NULL,
            tier_index INTEGER NOT NULL,
            min_input INTEGER, max_input INTEGER,
            min_output INTEGER, max_output INTEGER,
            input_price REAL, output_price REAL,
            cache_write_price REAL, cache_read_price REAL,
            FOREIGN KEY (pricing_id) REFERENCES model_defs(id) ON DELETE CASCADE,
            PRIMARY KEY (pricing_id, tier_index)
        );
        CREATE TABLE IF NOT EXISTS model_runtime (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id INTEGER NOT NULL,
            start_time REAL NOT NULL,
            end_time REAL,
            FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_model_runtime_model ON model_runtime(model_id);
        CREATE INDEX IF NOT EXISTS idx_model_runtime_times ON model_runtime(start_time, end_time);
        CREATE TABLE IF NOT EXISTS log_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL CHECK (type IN ('system','model')),
            model_name TEXT,
            alias TEXT,
            start_time REAL NOT NULL,
            end_time REAL
        );
        CREATE INDEX IF NOT EXISTS idx_log_sessions_start ON log_sessions(start_time);
        CREATE TABLE IF NOT EXISTS log_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES log_sessions(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL,
            ts REAL NOT NULL,
            stream TEXT NOT NULL,
            level TEXT NOT NULL,
            text TEXT NOT NULL,
            UNIQUE (session_id, seq)
        );
        CREATE INDEX IF NOT EXISTS idx_log_lines_session ON log_lines(session_id, id);
        CREATE TABLE IF NOT EXISTS cloud_providers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            api_key TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            openai_base TEXT NOT NULL DEFAULT '',
            responses_base TEXT NOT NULL DEFAULT '',
            claude_base TEXT NOT NULL DEFAULT '',
            extra_headers TEXT NOT NULL DEFAULT '{}',
            ord INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS cloud_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER NOT NULL REFERENCES cloud_providers(id) ON DELETE CASCADE,
            model_name TEXT NOT NULL,
            support_cache INTEGER NOT NULL DEFAULT 0,
            dual_pricing INTEGER NOT NULL DEFAULT 0,
            peak_windows TEXT NOT NULL DEFAULT '[]',
            ord INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(provider_id, model_name)
        );
        CREATE TABLE IF NOT EXISTS cloud_price_tiers (
            model_id INTEGER NOT NULL REFERENCES cloud_models(id) ON DELETE CASCADE,
            slot TEXT NOT NULL,
            tier_index INTEGER NOT NULL,
            min_input INTEGER, max_input INTEGER, min_output INTEGER, max_output INTEGER,
            input_price REAL, output_price REAL, cache_write_price REAL, cache_read_price REAL,
            PRIMARY KEY (model_id, slot, tier_index)
        );
        CREATE TABLE IF NOT EXISTS cloud_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER NOT NULL REFERENCES cloud_providers(id) ON DELETE CASCADE,
            local_path TEXT NOT NULL UNIQUE,
            target_url TEXT NOT NULL,
            auth_style TEXT NOT NULL DEFAULT 'bearer',
            ord INTEGER NOT NULL DEFAULT 0
        );
    """)
    _ensure_column(conn, "model_requests", "source", "source TEXT NOT NULL DEFAULT 'local'")
    conn.commit()
    return Db(conn=conn, write_lock=threading.Lock())


def _migrate(conn: sqlite3.Connection) -> None:
    """只检测、不迁移:检测旧库结构特征(model_requests.ts 列 / model_pricing / model_scripts
    表)→ LegacySchemaError(明确诊断,优于静默半迁移)。新库 CREATE IF NOT EXISTS
    即终态。"""
    legacy = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name IN ('model_pricing','model_scripts')"
    ).fetchone()
    if legacy or "ts" in {r[1] for r in conn.execute("PRAGMA table_info(model_requests)")}:
        raise LegacySchemaError(
            "数据库为 v3.1 之前时代的旧结构,已不再支持自动迁移;"
            "请备份后删除 data/ 下数据库重建(配置经 WebUI 重新录入),或留在 v3.0 使用"
        )


@dataclass(frozen=True, slots=True)
class ModelDataStats:
    """单模型积累数据量(请求 + 运行段)。"""

    request_count: int
    has_runtime_data: bool


@dataclass(frozen=True, slots=True)
class StorageStats:
    """数据库存储统计(数据管理页)。size_bytes 由调用方传入(API 层从 resolved_db 取)。"""

    size_bytes: int | None
    total_requests: int
    total_models_with_data: int
    models_data: dict[str, ModelDataStats]


def storage_stats(db: Db, *, configured: set[str], size_bytes: int | None = None) -> StorageStats:
    """数据库存储统计(数据管理页)。models_data = 配置模型 ∪ 数据库模型的并集:
    配置但无记录的模型显示 0 请求/无运行段(与 legacy 表格一致)。孤立模型(仅在
    数据库,不在配置)同样列出。total_models_with_data = 请求 > 0 或有运行段的模型数。"""
    total_requests = int(db.conn.execute("SELECT COUNT(*) FROM model_requests").fetchone()[0])
    runtime_ids = {
        r["model_id"] for r in db.conn.execute("SELECT DISTINCT model_id FROM model_runtime")
    }
    stats: dict[str, tuple[int, bool]] = {}
    rows = db.conn.execute(
        "SELECT m.original_name AS name, m.id AS mid, "
        "(SELECT COUNT(*) FROM model_requests r WHERE r.model_id = m.id) AS rc "
        "FROM models m"
    ).fetchall()
    for row in rows:
        stats[row["name"]] = (int(row["rc"]), row["mid"] in runtime_ids)
    models_data: dict[str, ModelDataStats] = {}
    for name in sorted(set(configured) | set(stats)):
        rc, has_runtime = stats.get(name, (0, False))
        models_data[name] = ModelDataStats(request_count=rc, has_runtime_data=has_runtime)
    total_models_with_data = sum(
        1 for st in models_data.values() if st.request_count > 0 or st.has_runtime_data
    )
    return StorageStats(
        size_bytes=size_bytes,
        total_requests=total_requests,
        total_models_with_data=total_models_with_data,
        models_data=models_data,
    )


def orphaned_models(db: Db, configured: set[str]) -> list[str]:
    """孤立模型 = models 表存在但不在当前配置(primary_name 集合)中。升序。"""
    names = [r["original_name"] for r in db.conn.execute("SELECT original_name FROM models")]
    return sorted(n for n in names if n not in configured)


def delete_model_data(db: Db, model_name: str) -> bool:
    """删除模型全部积累数据(外键级联清 model_requests/model_runtime)。
    未知名称 → False。删除 commit 后自动 VACUUM + wal_checkpoint(TRUNCATE) 回收空间;
    VACUUM 失败仅 warning(legacy 同款;:memory: DB 的 VACUUM 亦被吞,不阻塞删除)。"""
    with db.write_lock:
        cur = db.conn.execute("DELETE FROM models WHERE original_name = ?", (model_name,))
        db.conn.commit()
        if cur.rowcount == 0:
            return False
    try:
        db.conn.execute("VACUUM")
        db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as e:  # noqa: BLE001 — VACUUM 失败不影响删除结果
        logger.warning("VACUUM 失败(不影响删除结果): %s", e)
    return True
