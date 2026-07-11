"""DB-backed config store. 单一源 = 数据库(spec D4)。本模块逐步填充:settings KV → 模型世界读写
→ 脚本物化 → ConfigStore → bootstrap。config.py 的 load() 被复用为 YAML→DB 一次性导入器。"""
from __future__ import annotations

from llm_manager.data.persistence import Db


def _upsert_locked(db: Db, key: str, value: str) -> None:
    """caller MUST hold db.write_lock(threading.Lock 不可重入)。"""
    db.conn.execute(
        "INSERT INTO system_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
        (key, value),
    )


def set_setting(db: Db, key: str, value: str) -> None:
    with db.write_lock:
        _upsert_locked(db, key, value)
        db.conn.commit()


def get_setting(db: Db, key: str) -> str | None:
    row = db.conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def get_all_settings(db: Db) -> dict[str, str]:
    return {row["key"]: row["value"] for row in db.conn.execute("SELECT key, value FROM system_settings")}
