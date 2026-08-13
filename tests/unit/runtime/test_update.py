"""update.py 单测:本地 git 仓库 + 本地 bare origin,全程无网络。

验证标签版本身份、ff-only 严格语义(脏/分叉拒绝)、apply 拉取到远端、
非 git 仓库 / 无标签的回退。git 为环境依赖(仓库本身即 git 项目,必装)。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from llm_manager.runtime.update import UpdateError, apply_update, check_update


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "t@t.dev")
    _git(path, "config", "user.name", "t")


def _commit(path: Path, msg: str) -> str:
    (path / "file.txt").write_text(msg, encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-m", msg)
    return _git(path, "rev-parse", "--short", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """带本地 bare origin 的工作仓库(HEAD=v1.0.0),返回工作仓库根。"""
    work = tmp_path / "work"
    _init_repo(work)
    _commit(work, "c1")
    _git(work, "tag", "v1.0.0")
    origin = tmp_path / "origin.git"
    _git(work, "init", "--bare", str(origin))
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "origin", "main", "--tags")
    return work


def _advance_origin(repo: Path) -> str:
    """远端加一个提交 + 新标签 v1.1.0,返回新提交短 SHA(经镜像克隆,不碰工作仓库)。"""
    origin_url = _git(repo, "remote", "get-url", "origin")
    mirror = repo.parent / "mirror"
    _git(repo.parent, "clone", origin_url, str(mirror))
    _git(mirror, "config", "user.email", "t@t.dev")
    _git(mirror, "config", "user.name", "t")
    sha = _commit(mirror, "c2")
    _git(mirror, "tag", "v1.1.0")
    _git(mirror, "push", "origin", "main", "--tags")
    return sha


def test_uptodate(repo: Path) -> None:
    st = check_update(repo)
    assert st.ok and not st.error
    assert st.up_to_date and not st.available
    assert not st.dirty and not st.conflicted
    assert st.current_version == "v1.0.0"
    assert st.latest_version == "v1.0.0"
    assert st.commits_behind == 0
    assert st.current_sha and st.latest_sha == st.current_sha


def test_update_available(repo: Path) -> None:
    remote_sha = _advance_origin(repo)
    st = check_update(repo)
    assert st.ok
    assert st.available and not st.up_to_date
    assert st.current_version == "v1.0.0"
    assert st.latest_version == "v1.1.0"
    assert st.latest_sha == remote_sha
    assert st.commits_behind == 1


def test_dirty_blocks_check_and_apply(repo: Path) -> None:
    _advance_origin(repo)
    head_before = _git(repo, "rev-parse", "--short", "HEAD")
    (repo / "untracked.txt").write_text("x", encoding="utf-8")
    st = check_update(repo)
    assert st.dirty and not st.available
    with pytest.raises(UpdateError, match="未提交改动"):
        apply_update(repo)
    assert _git(repo, "rev-parse", "--short", "HEAD") == head_before  # 拒绝时不碰工作树


def test_conflicted_rejects_apply(repo: Path) -> None:
    _advance_origin(repo)
    _commit(repo, "local-only")  # 本地提交与远端分叉
    st = check_update(repo)
    assert st.conflicted and not st.available
    with pytest.raises(UpdateError, match="分叉"):
        apply_update(repo)


def test_apply_ff_only_updates_to_remote(repo: Path) -> None:
    remote_sha = _advance_origin(repo)
    new_sha = apply_update(repo)
    assert new_sha == remote_sha
    assert _git(repo, "rev-parse", "--short", "HEAD") == remote_sha
    assert not _git(repo, "status", "--porcelain")
    st = check_update(repo)
    assert st.up_to_date and st.current_version == "v1.1.0"


def test_not_a_git_repo(tmp_path: Path) -> None:
    st = check_update(tmp_path)
    assert not st.ok
    assert st.error and "git" in st.error


def test_no_tags_is_still_ok(tmp_path: Path) -> None:
    work = tmp_path / "notags"
    _init_repo(work)
    _commit(work, "c1")
    origin = tmp_path / "origin.git"
    _git(work, "init", "--bare", str(origin))
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "origin", "main")
    st = check_update(work)
    assert st.ok
    assert st.current_version == "未打标签"
    assert st.latest_version is None
    assert st.up_to_date
