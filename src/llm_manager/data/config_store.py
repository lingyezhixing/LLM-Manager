"""DB-backed config store. 单一源 = 数据库(spec D4)。本模块逐步填充:settings KV → 模型世界读写
→ ConfigStore → bootstrap。config.py 的 load() 被复用为 YAML→DB 一次性导入器。"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path

from llm_manager import config
from llm_manager.config import (
    PROGRAM_DEFAULTS,
    RETENTION_DEFAULTS,
    AppConfig,
    Command,
    ModelConfig,
    Pricing,
    PricingTier,
    ProgramConfig,
    Scheme,
    WakeOnLanConfig,
)
from llm_manager.data.persistence import Db

logger = logging.getLogger(__name__)


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


def set_settings(db: Db, updates: dict[str, str]) -> None:
    """多键原子写:单锁单 commit,失败 rollback(防 partial 被后续 commit 冲刷)。"""
    with db.write_lock:
        try:
            for k, v in updates.items():
                _upsert_locked(db, k, v)
            db.conn.commit()
        except Exception:
            db.conn.rollback()
            raise


def get_setting(db: Db, key: str) -> str | None:
    row = db.conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def get_all_settings(db: Db) -> dict[str, str]:
    return {row["key"]: row["value"] for row in db.conn.execute("SELECT key, value FROM system_settings")}


def _int_setting(s: dict[str, str], key: str, default: int) -> int:
    """防御手改 DB:retention 键非整数回退默认,防 retention 读取崩溃。"""
    raw = s.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid setting %s=%r, falling back to %d", key, raw, default)
        return default


def _delete_model_world_locked(db: Db) -> None:
    """清空模型世界(model_defs CASCADE 级联清 aliases/schemes/tiers)。caller 持锁。"""
    db.conn.execute("DELETE FROM model_defs")


def _write_appconfig_locked(db: Db, cfg: AppConfig) -> None:
    """caller MUST hold db.write_lock。全量替换模型世界 + upsert program/wol/claude。
    失败 rollback——共享写连接上未回滚的 partial 会被后续无关 commit 冲刷为脏数据。"""
    try:
        p = cfg.program
        _upsert_locked(db, "host", p.host)
        _upsert_locked(db, "port", str(p.port))
        _upsert_locked(db, "alive_time", str(p.alive_time))
        _upsert_locked(db, "log_level", p.log_level)
        _upsert_locked(db, "log_retention_days", str(p.log_retention_days))
        _upsert_locked(db, "log_retention_count", str(p.log_retention_count))
        if p.claude_settings_path is not None:
            _upsert_locked(db, "claude_settings_path", p.claude_settings_path)
        if cfg.wol is not None:
            _upsert_locked(db, "wol_broadcast", cfg.wol.broadcast_address)
            _upsert_locked(db, "wol_mac", cfg.wol.mac_address)
        _upsert_locked(db, "claude_configs", json.dumps(cfg.claude_configs, ensure_ascii=False))

        _delete_model_world_locked(db)
        for ord_idx, (name, m) in enumerate(cfg.models.items()):
            cur = db.conn.execute(
                "INSERT INTO model_defs (name, mode, port, auto_start, pricing_type, hourly_price, support_cache, ord) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (name, m.mode, m.port, int(m.auto_start),
                 m.pricing.pricing_type, m.pricing.hourly_price, int(m.pricing.support_cache), ord_idx),
            )
            mid = cur.lastrowid
            assert mid is not None
            for a_ord, alias in enumerate(m.aliases):
                db.conn.execute(
                    "INSERT INTO model_aliases (model_id, alias, ord) VALUES (?,?,?)",
                    (mid, alias, a_ord),
                )
            for s_ord, (src, scheme) in enumerate(m.schemes.items()):
                c = scheme.command
                command_json = json.dumps({"exe": c.exe, "args": list(c.args), "env": c.env,
                                           "cwd": c.cwd, "conda_env": c.conda_env})
                db.conn.execute(
                    "INSERT INTO model_schemes (model_id, config_source, required_devices, memory_mb, command, ord) "
                    "VALUES (?,?,?,?,?,?)",
                    (mid, src, json.dumps(sorted(scheme.required_devices)),
                     json.dumps(scheme.memory_mb), command_json, s_ord),
                )
            db.conn.execute("DELETE FROM pricing_tiers WHERE pricing_id=?", (mid,))
            for t in m.pricing.tiers:
                db.conn.execute(
                    "INSERT INTO pricing_tiers (pricing_id, tier_index, min_input, max_input, "
                    "min_output, max_output, input_price, output_price, "
                    "cache_write_price, cache_read_price) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (mid, t.tier_index, t.min_input, t.max_input, t.min_output, t.max_output,
                     t.input_price, t.output_price,
                     t.cache_write_price, t.cache_read_price),
                )
        db.conn.commit()
    except Exception:
        db.conn.rollback()
        raise


def write_appconfig(db: Db, cfg: AppConfig) -> None:
    """信任的内部/导入写路径(导入前已 validate,故不校验)。
    失败原子回滚——见 `_write_appconfig_locked`。"""
    with db.write_lock:
        _write_appconfig_locked(db, cfg)


def _read_appconfig_locked(db: Db) -> AppConfig:
    """caller MUST hold db.write_lock(与 _write_appconfig_locked 共用一把锁 → 多 SELECT 天然一致)。"""
    s = get_all_settings(db)
    program = ProgramConfig(
        host=s.get("host", PROGRAM_DEFAULTS["host"]),
        port=_int_setting(s, "port", int(PROGRAM_DEFAULTS["port"])),
        alive_time=_int_setting(s, "alive_time", int(PROGRAM_DEFAULTS["alive_time"])),
        log_level=s.get("log_level", PROGRAM_DEFAULTS["log_level"]),
        claude_settings_path=s.get("claude_settings_path"),
        log_retention_days=_int_setting(s, "log_retention_days", int(RETENTION_DEFAULTS["log_retention_days"])),
        log_retention_count=_int_setting(s, "log_retention_count", int(RETENTION_DEFAULTS["log_retention_count"])),
    )
    wol = None
    if "wol_broadcast" in s and "wol_mac" in s:
        wol = WakeOnLanConfig(s["wol_broadcast"], s["wol_mac"])
    claude_configs: dict = json.loads(s.get("claude_configs", "{}"))

    models: dict[str, ModelConfig] = {}
    for row in db.conn.execute(
            "SELECT id, name, mode, port, auto_start, pricing_type, hourly_price, support_cache "
            "FROM model_defs ORDER BY ord"):
        mid = row["id"]
        aliases = tuple(r["alias"] for r in db.conn.execute(
            "SELECT alias FROM model_aliases WHERE model_id = ? ORDER BY ord", (mid,)))
        schemes: dict[str, Scheme] = {}
        for srow in db.conn.execute(
                "SELECT config_source, required_devices, memory_mb, command "
                "FROM model_schemes WHERE model_id = ? ORDER BY ord", (mid,)):
            d = json.loads(srow["command"] or "{}")
            command = Command(exe=d.get("exe", ""), args=tuple(d.get("args", [])),
                              env=dict(d.get("env", {})), cwd=d.get("cwd"), conda_env=d.get("conda_env"))
            schemes[srow["config_source"]] = Scheme(
                config_source=srow["config_source"],
                required_devices=frozenset(json.loads(srow["required_devices"])),
                command=command,
                memory_mb=dict(json.loads(srow["memory_mb"])),
            )
        tiers = tuple(
            PricingTier(
                tier_index=tr["tier_index"], min_input=tr["min_input"], max_input=tr["max_input"],
                min_output=tr["min_output"], max_output=tr["max_output"],
                input_price=tr["input_price"], output_price=tr["output_price"],
                cache_write_price=tr["cache_write_price"], cache_read_price=tr["cache_read_price"])
            for tr in db.conn.execute(
                "SELECT tier_index, min_input, max_input, min_output, max_output, "
                "input_price, output_price, cache_write_price, cache_read_price "
                "FROM pricing_tiers WHERE pricing_id=? ORDER BY tier_index", (mid,)))
        pricing = Pricing(
            pricing_type=row["pricing_type"],
            hourly_price=row["hourly_price"],
            support_cache=bool(row["support_cache"]),
            tiers=tiers,
        )
        models[row["name"]] = ModelConfig(
            primary_name=row["name"], aliases=aliases, mode=row["mode"],
            port=row["port"], auto_start=bool(row["auto_start"]), schemes=schemes, pricing=pricing,
        )
    return AppConfig(program=program, models=models, wol=wol, claude_configs=claude_configs)


class ModelNotFound(KeyError):
    """CRUD: 指定 name 不存在(→ 404)。"""


class ModelExists(Exception):
    """CRUD: 指定 name 已存在(→ 409)。"""


class ConfigValidationFailed(Exception):
    """CRUD: mutate 后 config.validate 失败(→ 422)。携带 errors 列表。"""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def mutate_appconfig(db: Db, fn: Callable[[AppConfig], AppConfig]) -> AppConfig:
    """锁内原子读-改-写:read → fn(cfg)→cfg' → validate → write。

    fn: ``AppConfig -> AppConfig``,用 dataclasses.replace 在 frozen 快照上构造新实例;
    可 raise ModelNotFound / ModelExists(存在性检查,404/409 语义)。
    validate 失败 raise ConfigValidationFailed(不落库)。
    成功返新快照(caller 负责 store.reload() 刷缓存)。

    用锁-free 的 _read/_write_appconfig_locked(非重入 Lock:不能在此调公共 read/write_appconfig)。
    """
    with db.write_lock:
        cfg = _read_appconfig_locked(db)
        new_cfg = fn(cfg)
        errors = config.validate(new_cfg)
        if errors:
            raise ConfigValidationFailed(errors)
        _write_appconfig_locked(db, new_cfg)
        return new_cfg


def read_appconfig(db: Db) -> AppConfig:
    """加锁读(串行化 writer → 多 SELECT 一致;避免单共享连接并发事务冲突)。read_appconfig 低频
    (仅 ConfigStore reload/init),锁竞争可忽略。"""
    with db.write_lock:
        return _read_appconfig_locked(db)


class ConfigStore:
    """DB-backed config holder。frozen snapshot() 是消费方接口(缓存,不每次读库);
    reload() 重读 DB(P1 写回后调用)。"""

    def __init__(self, db: Db) -> None:
        self._db = db
        self._snapshot = read_appconfig(db)

    def snapshot(self) -> AppConfig:
        return self._snapshot

    def reload(self) -> AppConfig:
        self._snapshot = read_appconfig(self._db)
        return self._snapshot


ENV_MAP: dict[str, str] = {
    "LLM_MANAGER_HOST": "host",
    "LLM_MANAGER_PORT": "port",
    "LLM_MANAGER_ALIVE_TIME": "alive_time",
    "LLM_MANAGER_LOG_LEVEL": "log_level",
}

DEFAULTS: dict[str, str] = {
    **PROGRAM_DEFAULTS,
    **RETENTION_DEFAULTS,
    "claude_configs": "{}",
}


def is_initialized(db: Db) -> bool:
    row = db.conn.execute("SELECT COUNT(*) AS n FROM system_settings").fetchone()
    return int(row["n"]) > 0


def seed_defaults(db: Db) -> None:
    with db.write_lock:
        for k, v in DEFAULTS.items():
            _upsert_locked(db, k, v)
        db.conn.commit()


_INT_ENV_KEYS = {"LLM_MANAGER_PORT", "LLM_MANAGER_ALIVE_TIME"}


def apply_env_overrides(db: Db) -> None:
    """对每个已设置的 LLM_MANAGER_* upsert 写库(env 不直接覆盖运行变量;运行时只读 DB)。

    整型 env(LLM_MANAGER_PORT / ALIVE_TIME)在写库前校验,坏值直接抛 ValueError——
    否则坏值会持久化进 DB,导致 read_appconfig 的 int() 崩溃且形成持续 boot-loop。"""
    for env_key in _INT_ENV_KEYS:
        val = os.environ.get(env_key)
        if val is not None:
            try:
                int(val)
            except ValueError:
                raise ValueError(f"{env_key}={val!r} must be an integer")
    with db.write_lock:
        try:
            for env_key, setting_key in ENV_MAP.items():
                val = os.environ.get(env_key)
                if val is not None:
                    _upsert_locked(db, setting_key, val)
            db.conn.commit()
        except Exception:
            db.conn.rollback()
            raise


def initialize(db: Db, legacy_yaml: Path | None) -> None:
    """启动期 provision:空库 → 导入 legacy_yaml(校验失败即抛,不落脏数据)或种子默认;然后 env 写库。"""
    if not is_initialized(db):
        if legacy_yaml is not None and legacy_yaml.exists():
            cfg = config.load(legacy_yaml)
            errors = config.validate(cfg)
            if errors:
                raise ValueError("Invalid legacy config:\n" + "\n".join(f"  - {e}" for e in errors))
            write_appconfig(db, cfg)
        else:
            seed_defaults(db)
    apply_env_overrides(db)
