"""测试用 AppConfig 程序化构建器(DB 接管后无 YAML;直接构造 frozen dataclasses)。

按需组装模型/程序参数,配合
``config_store.write_appconfig(db, cfg)`` 在测试里预置 DB 状态。经 pyproject
``pythonpath=["tests"]`` 以 ``from helpers import cfg, model, scheme`` 导入。
"""

from __future__ import annotations

from llm_manager.config import AppConfig, Command, ModelConfig, ProgramConfig, Scheme

_PROG_DEFAULTS = {"host": "0.0.0.0", "port": 8080, "alive_time": 60, "log_level": "INFO"}


def scheme(
    source: str = "S",
    *,
    devices: tuple[str, ...] = (),
    exe: str = "a.bat",
    memory_mb: dict[str, int] | None = None,
) -> Scheme:
    """设备方案(设备名存储原样,匹配时归一化)。"""
    return Scheme(source, frozenset(devices), Command(exe=exe), dict(memory_mb or {}))


def model(
    aliases: tuple[str, ...],
    port: int,
    *,
    mode: str = "Chat",
    auto_start: bool = False,
    schemes: dict[str, Scheme] | None = None,
) -> ModelConfig:
    return ModelConfig(
        aliases=aliases,
        mode=mode,
        port=port,
        auto_start=auto_start,
        schemes=schemes or {},
    )


def cfg(
    models: dict[str, ModelConfig] | None = None,
    *,
    program: dict | None = None,
    wol=None,
    claude_configs: dict[str, dict[str, str]] | None = None,
) -> AppConfig:
    return AppConfig(
        program=ProgramConfig(**{**_PROG_DEFAULTS, **(program or {})}),
        models=models or {},
        wol=wol,
        claude_configs=claude_configs or {},
    )
