"""运行时版本号解析。

优先读源码树 pyproject.toml(editable 开发 / Docker editable 挂载更新即时生效),
回退已安装分发包元数据(标准 ``pip install .`` 场景),再回退 ``"unknown"``。

不依赖"升版本后重新 pip install":只要源码树 pyproject.toml 存在且能读到,
改 pyproject + 重启进程即生效——避免 importlib.metadata 读到滞后一个版本的
安装期元数据快照(那是旧实现显示 3.0.0a1 而 pyproject 已是 a2 的根因)。
"""

import logging
import tomllib
from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _metadata_version
from pathlib import Path

_PACKAGE_NAME = "llm-manager"
_UNKNOWN = "unknown"
# 向上查找 pyproject 的最大层数。src/llm_manager/version.py → 仓库根为 3 层,
# 余量留给 PEP 660 editable wheel 的额外 __editable__ 路径段。
_MAX_DEPTH = 7

logger = logging.getLogger(__name__)


def _resolve_version(
    start_file: Path | str | None = None,
    metadata_lookup: object = _metadata_version,
) -> str:
    """版本回退链:源码 pyproject → importlib 元数据 → unknown。

    * start_file:查找起点,默认本文件(从 version.py 向上找仓库根 pyproject)。
    * metadata_lookup:默认为 :func:`importlib.metadata.version`,测试可注入。

    遇到路径上第一个 pyproject.toml 即评估——命中本项目则返回其 version;
    否则(归属不符 / 解析失败 / 缺 version)停止向上,落到 metadata 回退。
    """
    start = Path(start_file or __file__).resolve()
    for parent in start.parents[:_MAX_DEPTH]:
        candidate = parent / "pyproject.toml"
        if not candidate.is_file():
            continue
        try:
            data = tomllib.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            break  # 文件在但读不了/解析失败:无法确认归属,保守停止向上
        project = data.get("project", {})
        if project.get("name") == _PACKAGE_NAME:
            ver = project.get("version")
            if isinstance(ver, str) and ver:
                return ver
        break  # name 不符(碰到别人的 pyproject)或本项目 pyproject 缺 version

    try:
        return metadata_lookup(_PACKAGE_NAME)  # type: ignore[no-any-return]
    except PackageNotFoundError:
        return _UNKNOWN
    except Exception:
        logger.debug("metadata version lookup failed", exc_info=True)
        return _UNKNOWN


@lru_cache(maxsize=1)
def get_version() -> str:
    """进程内缓存的当前版本号。"""
    return _resolve_version()
