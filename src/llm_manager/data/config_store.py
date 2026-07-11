"""DB-backed config store. 单一源 = 数据库(spec D4)。本模块逐步填充:settings KV → 模型世界读写
→ 脚本物化 → ConfigStore → bootstrap。config.py 的 load() 被复用为 YAML→DB 一次性导入器。"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from llm_manager.config import AppConfig, ModelConfig, ProgramConfig, Scheme, WakeOnLanConfig
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
    """全量替换模型世界 + upsert program/wol/claude。脚本:read_scripts 时读取 script_path 文件内容。
    失败显式 rollback——共享写连接上,未回滚的 partial 事务会被后续无关 commit 冲刷为脏数据。"""
    with db.write_lock:
        try:
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
        except Exception:
            db.conn.rollback()
            raise


_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_name(name: str) -> str:
    return _SAFE.sub("_", name)


def _materialize(model_name: str, scheme_source: str, content: str, content_hash: str,
                 fallback_path: Path, scripts_dir: Path) -> Path:
    """content 非空 → 物化到 scripts_dir/<model>/<scheme>.<ext>(hash 变更才重写);content 空 → 回退 path。"""
    if not content:
        return fallback_path
    ext = ".bat" if os.name == "nt" else ".sh"
    target = scripts_dir / _safe_name(model_name) / f"{_safe_name(scheme_source)}{ext}"
    if not target.exists() or _sha256(target.read_text(encoding="utf-8")) != content_hash:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return target


def read_appconfig(db: Db, *, scripts_dir: Path = Path("data/scripts")) -> AppConfig:
    s = get_all_settings(db)
    program = ProgramConfig(
        host=s.get("host", "0.0.0.0"),
        port=int(s.get("port", "8080")),
        alive_time=int(s.get("alive_time", "60")),
        log_level=s.get("log_level", "INFO"),
        log_dir=s.get("log_dir", "logs"),
        db_path=s.get("db_path", "data/llm_manager.db"),
        claude_settings_path=s.get("claude_settings_path"),
    )
    wol = None
    if "wol_broadcast" in s and "wol_mac" in s:
        wol = WakeOnLanConfig(s["wol_broadcast"], s["wol_mac"])
    claude_configs: dict = json.loads(s.get("claude_configs", "{}"))

    models: dict[str, ModelConfig] = {}
    for row in db.conn.execute("SELECT id, name, mode, port, auto_start FROM model_defs ORDER BY ord"):
        mid = row["id"]
        aliases = tuple(r["alias"] for r in db.conn.execute(
            "SELECT alias FROM model_aliases WHERE model_id = ? ORDER BY ord", (mid,)))
        schemes: dict[str, Scheme] = {}
        for srow in db.conn.execute(
                "SELECT id, config_source, required_devices, memory_mb "
                "FROM model_schemes WHERE model_id = ? ORDER BY ord", (mid,)):
            sid = srow["id"]
            script_row = db.conn.execute(
                "SELECT path, content, content_hash FROM model_scripts WHERE scheme_id = ?", (sid,)).fetchone()
            fallback = Path(script_row["path"]) if script_row else Path("")
            content = script_row["content"] if script_row else ""
            chash = script_row["content_hash"] if script_row else ""
            script_path = _materialize(row["name"], srow["config_source"], content, chash, fallback, scripts_dir)
            schemes[srow["config_source"]] = Scheme(
                config_source=srow["config_source"],
                required_devices=frozenset(json.loads(srow["required_devices"])),
                script_path=script_path,
                memory_mb=dict(json.loads(srow["memory_mb"])),
            )
        models[row["name"]] = ModelConfig(
            primary_name=row["name"], aliases=aliases, mode=row["mode"],
            port=row["port"], auto_start=bool(row["auto_start"]), schemes=schemes,
        )
    return AppConfig(program=program, models=models, wol=wol, claude_configs=claude_configs)
