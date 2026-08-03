"""DB open/schema/migration + storage stats & orphaned-model maintenance。usage 聚合在 data/usage.py,日志存储 in data/logs.py。"""
from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Db:
    conn: sqlite3.Connection
    write_lock: threading.Lock


def open_db(path: Path) -> Db:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
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
    """)
    _migrate(conn)
    conn.commit()
    return Db(conn=conn, write_lock=threading.Lock())


def _migrate(conn: sqlite3.Connection) -> None:
    """Drop the obsolete ``ts`` column if present (Round-2 era). Option A folds the
    timestamp back into start_time/end_time (now wall-clock again, as in legacy). No-op
    on fresh DBs.

    显式事务包裹全程:Python sqlite3 legacy 模式下 DDL 逐条 autocommit,无事务则中途
    崩溃会留下半迁移状态(数据未搬完而表已删,或 pricing_tiers_new 残留)。BEGIN 后
    DDL 不再隐式提交,整段原子——异常 ROLLBACK 回退,下次 open_db 从头重跑。"""
    conn.execute("BEGIN")
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(model_requests)")}
        if "ts" in cols:
            # SQLite refuses DROP COLUMN while an index references it; drop the index first.
            conn.execute("DROP INDEX IF EXISTS idx_model_requests_ts")
            conn.execute("ALTER TABLE model_requests DROP COLUMN ts")
        # 回改:support_cache 从阶梯级上移到模型级(model_pricing)。
        # 旧库:model_pricing 无该列则补;pricing_tiers 有该列则删(SQLite ≥3.35 支持 DROP COLUMN)。
        # 新库已无 model_pricing(代码优化 2026-08-03 并入 model_defs)→ 存在性判定防 no such table。
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_pricing'").fetchone():
            mp_cols = {row[1] for row in conn.execute("PRAGMA table_info(model_pricing)")}
            if "support_cache" not in mp_cols:
                conn.execute("ALTER TABLE model_pricing ADD COLUMN support_cache INTEGER NOT NULL DEFAULT 0")
        pt_cols = {row[1] for row in conn.execute("PRAGMA table_info(pricing_tiers)")}
        if "support_cache" in pt_cols:
            conn.execute("ALTER TABLE pricing_tiers DROP COLUMN support_cache")
        # === 代码优化(2026-08-03):model_scripts → model_schemes.command 列 ===
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_schemes'").fetchone():
            sc_cols = {row[1] for row in conn.execute("PRAGMA table_info(model_schemes)")}
            if "command" not in sc_cols:
                conn.execute("ALTER TABLE model_schemes ADD COLUMN command TEXT")
                conn.execute(
                    "UPDATE model_schemes SET command = "
                    "(SELECT s.command FROM model_scripts s WHERE s.scheme_id = model_schemes.id)")
                conn.execute("DROP TABLE IF EXISTS model_scripts")
            # 无 scripts 行的 scheme(或此前中断残留的 NULL)→ 归一为新库 DEFAULT '{}' 形态
            conn.execute("UPDATE model_schemes SET command='{}' WHERE command IS NULL")
        # === model_pricing → model_defs 3 列 ===
        md_cols = {row[1] for row in conn.execute("PRAGMA table_info(model_defs)")}
        if "pricing_type" not in md_cols:
            conn.execute("ALTER TABLE model_defs ADD COLUMN pricing_type TEXT")
            conn.execute("ALTER TABLE model_defs ADD COLUMN hourly_price REAL")
            conn.execute("ALTER TABLE model_defs ADD COLUMN support_cache INTEGER")
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_pricing'").fetchone():
                conn.execute(
                    "UPDATE model_defs SET "
                    "pricing_type = COALESCE((SELECT pricing_type FROM model_pricing WHERE model_id = model_defs.id), 'tier'), "
                    "hourly_price = COALESCE((SELECT hourly_price FROM model_pricing WHERE model_id = model_defs.id), 0), "
                    "support_cache = COALESCE((SELECT support_cache FROM model_pricing WHERE model_id = model_defs.id), 0)")
        # 无 model_pricing 行的模型 → 归一为新库 NOT NULL DEFAULT 形态
        conn.execute("UPDATE model_defs SET pricing_type='tier', hourly_price=0, support_cache=0 WHERE pricing_type IS NULL")
        # === pricing_tiers 重建改 FK → model_defs(id)(必须在 DROP model_pricing 前 COPY) ===
        pt_fks = {row[2] for row in conn.execute("PRAGMA foreign_key_list(pricing_tiers)")}
        if "model_pricing" in pt_fks:
            conn.execute("""
                CREATE TABLE pricing_tiers_new (
                    pricing_id INTEGER NOT NULL,
                    tier_index INTEGER NOT NULL,
                    min_input INTEGER, max_input INTEGER,
                    min_output INTEGER, max_output INTEGER,
                    input_price REAL, output_price REAL,
                    cache_write_price REAL, cache_read_price REAL,
                    FOREIGN KEY (pricing_id) REFERENCES model_defs(id) ON DELETE CASCADE,
                    PRIMARY KEY (pricing_id, tier_index)
                )""")
            conn.execute("INSERT INTO pricing_tiers_new SELECT * FROM pricing_tiers")
            conn.execute("DROP TABLE pricing_tiers")
            conn.execute("ALTER TABLE pricing_tiers_new RENAME TO pricing_tiers")
        # === 旧 model_pricing 表删除(数据已搬,此时 tiers 已重建) ===
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_pricing'").fetchone():
            conn.execute("DROP TABLE model_pricing")
        # === 心跳列(2026-08-03):崩溃残留收口用 last_active(≈死亡时刻)而非收口时刻 ===
        # 运行中会话每 30s 由 heartbeat_loop 落库;end_time 仍仅退出时写(NULL=运行中)。
        for tbl in ("log_sessions", "model_runtime"):
            hb_cols = {row[1] for row in conn.execute(f"PRAGMA table_info({tbl})")}
            if "last_active" not in hb_cols:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN last_active REAL")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass  # 无活跃事务(BEGIN 本身失败)时不掩盖原始错误
        raise
    conn.execute("COMMIT")


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
