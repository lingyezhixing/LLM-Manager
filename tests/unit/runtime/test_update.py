"""update.py 单测:本地 git 仓库 + 本地 bare origin,全程无网络。

验证标签版本身份、双目标(tag/commit)可用性与应用、ff-only 严格语义
(冲突拒绝 / 非冲突本地改动放行 / 分叉拒绝)、非 git 仓库 / 无标签回退。
git 为环境依赖(仓库本身即 git 项目,必装)。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from llm_manager.runtime.update import (
    UpdateError,
    _https_rewrite_args,
    apply_update,
    check_update,
)


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "t@t.dev")
    _git(path, "config", "user.name", "t")


def _init_bare(path: Path) -> None:
    """bare origin:git 版本间默认分支不一致(runner master/本机 main)会让后续
    clone 落到 unborn 分支、`push origin main` 报 "src refspec main does not match
    any"——显式把 HEAD 钉在 main,与默认分支解耦(2026-08-24 CI Ubuntu 全挂根因)。
    注意 init cwd 是 path.parent(path 尚不存在),symbolic-ref 才在 path 内。"""
    _git(path.parent, "init", "--bare", str(path))
    _git(path, "symbolic-ref", "HEAD", "refs/heads/main")


def _commit(path: Path, msg: str) -> str:
    (path / "file.txt").write_text(msg, encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-m", msg)
    return _git(path, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """带本地 bare origin 的工作仓库(HEAD=v1.0.0),返回工作仓库根。"""
    work = tmp_path / "work"
    _init_repo(work)
    _commit(work, "c1")
    _git(work, "tag", "v1.0.0")
    origin = tmp_path / "origin.git"
    _init_bare(origin)
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "origin", "main", "--tags")
    return work


def _advance_origin(repo: Path) -> tuple[str, str]:
    """远端加两个提交:c2 打标签 v1.1.0,其后 c3 未打标签。返回 (tag_sha, head_sha)。
    经镜像克隆操作,不碰工作仓库。"""
    origin_url = _git(repo, "remote", "get-url", "origin")
    mirror = repo.parent / "mirror"
    _git(repo.parent, "clone", origin_url, str(mirror))
    _git(mirror, "config", "user.email", "t@t.dev")
    _git(mirror, "config", "user.name", "t")
    tag_sha = _commit(mirror, "c2")
    _git(mirror, "tag", "v1.1.0")
    head_sha = _commit(mirror, "c3")
    _git(mirror, "push", "origin", "main", "--tags")
    return tag_sha[:7], head_sha[:7]


def _no_remote_repo(tmp_path: Path, name: str) -> Path:
    work = tmp_path / name
    _init_repo(work)
    _commit(work, "c1")
    return work


def test_uptodate(repo: Path) -> None:
    st = check_update(repo)
    assert st.ok and not st.error
    assert not st.dirty and not st.conflicted
    assert st.current_version == "v1.0.0"
    assert st.tag == "v1.0.0"
    assert st.commit_sha == st.current_sha
    assert not st.tag_available and not st.commit_available
    assert st.tag_behind == 0 and st.commit_behind == 0


def test_targets_available_and_apply(repo: Path) -> None:
    tag_sha, head_sha = _advance_origin(repo)
    st = check_update(repo)
    assert st.ok and not st.conflicted
    assert st.tag == "v1.1.0"
    assert st.commit_sha == head_sha
    assert st.tag_available and st.commit_available
    assert st.tag_behind == 1 and st.commit_behind == 2  # c2(标签)+ c3(未标签)

    # 先更新到标签 → 停在 c2(带标签),仍在 origin/main 之后
    assert apply_update(repo, target="tag") == tag_sha
    assert _git(repo, "rev-parse", "--short", "HEAD") == tag_sha
    after = check_update(repo)
    assert after.tag_available is False  # 已在最新标签上
    assert after.tag_behind == 0
    assert after.commit_available is True  # 仍落后未打标签的 c3
    assert after.commit_behind == 1

    # 再更新到提交 → 追上 origin/main 最前沿
    assert apply_update(repo, target="commit") == head_sha
    assert not _git(repo, "status", "--porcelain")
    done = check_update(repo)
    assert not done.commit_available and not done.tag_available
    assert done.commit_behind == 0 and done.tag_behind == 0
    assert done.current_version == "v1.1.0"


def test_untagged_commit_only_available_when_on_latest_tag(repo: Path) -> None:
    tag_sha, head_sha = _advance_origin(repo)
    apply_update(repo, target="tag")
    st = check_update(repo)
    assert st.tag_available is False
    assert st.commit_available is True
    assert st.commit_sha == head_sha and st.current_sha == tag_sha


def test_apply_unknown_target(repo: Path) -> None:
    with pytest.raises(UpdateError, match="未知更新目标"):
        apply_update(repo, target="release")


def test_dirty_nonconflicting_local_change_passes(repo: Path) -> None:
    _advance_origin(repo)
    (repo / "note.txt").write_text("local", encoding="utf-8")  # 更新不触碰的文件
    head = apply_update(repo, target="commit")
    assert head
    assert (repo / "note.txt").read_text(encoding="utf-8") == "local"  # 本地改动保留
    assert not _git(repo, "status", "--porcelain", "--", "file.txt")


def test_dirty_conflicting_local_change_refuses(repo: Path) -> None:
    _advance_origin(repo)
    head_before = _git(repo, "rev-parse", "--short", "HEAD")
    (repo / "file.txt").write_text("local-conflict", encoding="utf-8")  # 与更新同文件冲突
    with pytest.raises(UpdateError, match="冲突"):
        apply_update(repo, target="commit")
    assert _git(repo, "rev-parse", "--short", "HEAD") == head_before  # 拒绝时不碰 HEAD


def test_conflicted_rejects_both_targets(repo: Path) -> None:
    _advance_origin(repo)
    _commit(repo, "local-only")  # 本地提交与远端分叉
    st = check_update(repo)
    assert st.conflicted and not st.tag_available and not st.commit_available
    with pytest.raises(UpdateError, match="分叉"):
        apply_update(repo, target="commit")


def test_not_a_git_repo(tmp_path: Path) -> None:
    st = check_update(tmp_path)
    assert not st.ok
    assert st.supported is False
    assert st.error and "git" in st.error


def test_annotated_tag_not_false_positive(repo: Path) -> None:
    """HEAD 恰在最新带注释标签的提交上时,不得误报可更新(裸 rev-parse 返回 tag
    对象 SHA → 误判 tag_full != head → 点击后无谓重启)。"""
    _git(repo, "tag", "-a", "v1.0.1", "-m", "annotated release")  # 带注释标签
    _git(repo, "push", "origin", "main", "--tags")
    st = check_update(repo)
    assert st.tag == "v1.0.1"
    assert st.tag_available is False
    assert st.tag_behind == 0


def test_apply_noop_when_up_to_date_refuses(repo: Path) -> None:
    """已是最新时 apply 不得静默 no-op 并触发重启。"""
    with pytest.raises(UpdateError, match="无需更新"):
        apply_update(repo, target="commit")
    with pytest.raises(UpdateError, match="无需更新"):
        apply_update(repo, target="tag")


def test_check_offline_fetch_failure(repo: Path) -> None:
    """远端不可达 → ok=False 但 supported=True(功能仍在,可手动重试)。"""
    _git(repo, "remote", "set-url", "origin", "https://127.0.0.1:1/unreachable.git")
    st = check_update(repo)
    assert st.ok is False
    assert st.supported is True
    assert st.error and "拉取远端失败" in st.error


def test_git_missing_unsupported(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("llm_manager.runtime.update.shutil.which", lambda _name: None)
    st = check_update(tmp_path)
    assert not st.ok
    assert st.supported is False
    assert "git" in (st.error or "")


def test_https_rewrite_for_ssh_origin_when_no_ssh(monkeypatch, repo: Path) -> None:
    """容器缺 ssh + origin 为 scp 式 SSH URL → fetch 生成 HTTPS 重写(仅本次,不碰配置)。"""
    monkeypatch.setattr(
        "llm_manager.runtime.update.shutil.which",
        lambda name: "/usr/bin/git" if name == "git" else None,
    )
    _git(repo, "remote", "set-url", "origin", "git@github.com:someone/repo.git")
    assert _https_rewrite_args(repo) == ["-c", "url.https://github.com/.insteadOf=git@github.com:"]


def test_https_rewrite_noop_with_ssh_present(repo: Path) -> None:
    """本机有 ssh → 不重写(origin 为本地路径亦不重写)。"""
    assert _https_rewrite_args(repo) == []


def test_https_rewrite_noop_for_https_origin(monkeypatch, repo: Path) -> None:
    monkeypatch.setattr(
        "llm_manager.runtime.update.shutil.which",
        lambda name: "/usr/bin/git" if name == "git" else None,
    )
    _git(repo, "remote", "set-url", "origin", "https://github.com/someone/repo.git")
    assert _https_rewrite_args(repo) == []


def test_fetch_refreshes_force_moved_tag(tmp_path: Path) -> None:
    """远端发布标签被强移(改史/squash 重打)后,普通 fetch 残留本地旧标签 → 版本识别
    错位;`_fetch` 的 --tags --force 强制对齐远端标签。容器侧 HEAD 停在旧祖先提交,
    标签是 fetch 自动跟随带进来的(与实际场景一致)。"""
    orig = tmp_path / "orig"
    _init_repo(orig)
    _commit(orig, "c1")
    _git(orig, "tag", "v1.0.0")
    origin = tmp_path / "origin.git"
    _init_bare(origin)
    _git(orig, "remote", "add", "origin", str(origin))
    _git(orig, "push", "origin", "main", "--tags")

    # 容器 = 克隆(HEAD 停在 c1)
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", str(origin), str(clone))

    # 远端推进 c2 + v2.0.0;容器 fetch 自动跟随 v2.0.0(此后成为"旧标签"),HEAD 不动
    _commit(orig, "c2")
    _git(orig, "tag", "v2.0.0")
    _git(orig, "push", "origin", "main", "--tags")
    _git(clone, "fetch", "origin")

    # 远端改史 + 标签强移:reset 掉 c2,重建等价提交 c2'(不同 SHA),v2.0.0 强移过去
    _git(orig, "reset", "--soft", "HEAD~1")
    _commit(orig, "c2'")
    _git(orig, "tag", "-f", "v2.0.0")
    _git(orig, "push", "origin", "main", "--force")
    _git(orig, "push", "origin", "v2.0.0", "--force")

    # 复现错位:普通 fetch 不更新已存在标签,describe(origin/main) 沿 c2' 祖先回退 v1.0.0
    _git(clone, "fetch", "origin")
    assert _git(clone, "describe", "--tags", "--abbrev=0", "origin/main") == "v1.0.0"

    # 修复:check_update 内部 _fetch 带 --tags --force → 标签对齐远端,正确识别 v2.0.0
    st = check_update(clone)
    assert st.ok and not st.conflicted
    assert st.tag == "v2.0.0"
    assert st.tag_available is True
    assert st.commit_available is True
    assert st.commit_sha == _git(clone, "rev-parse", "--short", "origin/main")


def test_no_tags_commit_target_only(tmp_path: Path) -> None:
    work = tmp_path / "notags"
    _init_repo(work)
    _commit(work, "c1")
    origin = tmp_path / "origin.git"
    _init_bare(origin)
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "origin", "main")
    st = check_update(work)
    assert st.ok
    assert st.current_version == "未打标签"
    assert st.tag is None and not st.tag_available
    assert not st.commit_available  # 已是最新
    with pytest.raises(UpdateError, match="无标签"):
        apply_update(work, target="tag")
