"""DB-backed config store. 单一源 = 数据库(spec D4)。本模块逐步填充:settings KV → 模型世界读写
→ 脚本物化 → ConfigStore → bootstrap。config.py 的 load() 被复用为 YAML→DB 一次性导入器。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from llm_manager.config import AppConfig
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


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lang_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".bat", ".cmd"):
        return "bat"
    if suffix == ".sh":
        return "sh"
    return suffix.lstrip(".") or "bat"


def _delete_model_world_locked(db: Db) -> None:
    """清空模型世界(model_defs CASCADE 级联清 aliases/schemes/scripts/pricing/tiers)。caller 持锁。"""
    db.conn.execute("DELETE FROM model_defs")


def write_appconfig(db: Db, cfg: AppConfig, *, read_scripts: bool = True) -> None:
    """全量替换模型世界 + upsert program/wol/claude。脚本:read_scripts 时读取 script_path 文件内容。"""
    with db.write_lock:
        p = cfg.program
        _upsert_locked(db, "host", p.host)
        _upsert_locked(db, "port", str(p.port))
        _upsert_locked(db, "alive_time", str(p.alive_time))
        _upsert_locked(db, "log_level", p.log_level)
        _upsert_locked(db, "log_dir", p.log_dir)
        _upsert_locked(db, "db_path", p.db_path)
        if p.claude_settings_path is not None:
            _upsert_locked(db, "claude_settings_path", p.claude_settings_path)
        if cfg.wol is not None:
            _upsert_locked(db, "wol_broadcast", cfg.wol.broadcast_address)
            _upsert_locked(db, "wol_mac", cfg.wol.mac_address)
        _upsert_locked(db, "claude_configs", json.dumps(cfg.claude_configs, ensure_ascii=False))

        _delete_model_world_locked(db)
        for ord_idx, (name, m) in enumerate(cfg.models.items()):
            cur = db.conn.execute(
                "INSERT INTO model_defs (name, mode, port, auto_start, ord) VALUES (?,?,?,?,?)",
                (name, m.mode, m.port, int(m.auto_start), ord_idx),
            )
            mid = cur.lastrowid
            assert mid is not None
            for a_ord, alias in enumerate(m.aliases):
                db.conn.execute(
                    "INSERT INTO model_aliases (model_id, alias, ord) VALUES (?,?,?)",
                    (mid, alias, a_ord),
                )
            for s_ord, (src, scheme) in enumerate(m.schemes.items()):
                scur = db.conn.execute(
                    "INSERT INTO model_schemes (model_id, config_source, required_devices, memory_mb, ord) "
                    "VALUES (?,?,?,?,?)",
                    (mid, src,
                     json.dumps(sorted(scheme.required_devices)),
                     json.dumps(scheme.memory_mb),
                     s_ord),
                )
                sid = scur.lastrowid
                assert sid is not None
                content, chash = "", ""
                if read_scripts:
                    try:
                        content = Path(scheme.script_path).read_text(encoding="utf-8")
                        chash = _sha256(content)
                    except OSError:
                        content, chash = "", ""
                db.conn.execute(
                    "INSERT INTO model_scripts (scheme_id, path, content, content_hash, lang) VALUES (?,?,?,?,?)",
                    (sid, str(scheme.script_path), content, chash, _lang_for(scheme.script_path)),
                )
        db.conn.commit()
