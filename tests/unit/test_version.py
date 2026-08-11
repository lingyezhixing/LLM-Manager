"""version.py 回退链测试:pyproject 源 → importlib 元数据 → unknown。

打内部 `_resolve_version(start_file, metadata_lookup)`:注入假起点 / 假 metadata
查询,覆盖三条回退路径与 stop-on-first-pyproject 边界,不走 get_version 的缓存。
"""

import tomllib
from importlib.metadata import PackageNotFoundError
from pathlib import Path

from llm_manager.version import _resolve_version


def _repo_root_pyproject_version() -> str:
    """直接读仓库根 pyproject.toml 的 version,作测试 1 的动态期望值。"""
    # tests/unit/test_version.py → parents[2] = 仓库根
    root = Path(__file__).resolve().parents[2]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def _write_pyproject(dir_: Path, name: str, version: str | None = None) -> None:
    lines = ["[project]", f'name = "{name}"']
    if version is not None:
        lines.append(f'version = "{version}"')
    (dir_ / "pyproject.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _touch_start(tmp_path: Path) -> Path:
    """在 tmp_path/sub/ 下造一个假的 version.py 起点并返回。"""
    start = tmp_path / "sub" / "version.py"
    start.parent.mkdir(parents=True)
    start.touch()
    return start


def test_reads_version_from_source_pyproject():
    # 默认起点(version.py 自身 __file__)→ 命中仓库根 pyproject → 返回其 version。
    # 动态比对源码 pyproject 当前值,不硬编码版本号,升版本时本测试仍稳。
    assert _resolve_version() == _repo_root_pyproject_version()


def test_falls_back_to_metadata_when_pyproject_not_in_tree(tmp_path):
    # 起点在系统临时目录,向上无本项目 pyproject → 回退 metadata。
    assert _resolve_version(tmp_path / "v.py", metadata_lookup=lambda _n: "9.9.9") == "9.9.9"


def test_falls_back_to_unknown_when_neither_available(tmp_path):
    def raise_not_found(_name):
        raise PackageNotFoundError(_name)

    assert _resolve_version(tmp_path / "v.py", metadata_lookup=raise_not_found) == "unknown"


def test_stops_on_non_matching_pyproject(tmp_path):
    # 路径上先碰到 name="other-project" 的 pyproject → 停止向上,不误信,回退 metadata。
    _write_pyproject(tmp_path, name="other-project", version="0.0.1")
    assert _resolve_version(_touch_start(tmp_path), metadata_lookup=lambda _n: "1.0.0") == "1.0.0"


def test_skips_corrupt_toml_then_falls_back(tmp_path):
    # pyproject 存在但 TOML 非法 → 不抛异常,回退 metadata。
    (tmp_path / "pyproject.toml").write_text("not = = valid = toml", encoding="utf-8")
    assert _resolve_version(_touch_start(tmp_path), metadata_lookup=lambda _n: "2.0.0") == "2.0.0"


def test_name_match_is_hyphen_strict(tmp_path):
    # name 用下划线 "llm_manager" 而非连字符 "llm-manager" → 不匹配本项目 → 回退 metadata。
    _write_pyproject(tmp_path, name="llm_manager", version="3.0.0a2")
    assert _resolve_version(_touch_start(tmp_path), metadata_lookup=lambda _n: "3.0.0") == "3.0.0"
