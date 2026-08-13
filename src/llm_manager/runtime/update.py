"""Git 自更新编排(标签版本 + 严格 ff-only)。

版本以 git 标签为唯一事实源:当前版本 = HEAD 最近可达标签(``git describe --tags
--abbrev=0``),最新版本 = origin/main 最近可达标签(fetch 后,即"更新实际拿到的版本")。
更新 = ``git fetch origin`` + ``git merge --ff-only origin/main`` —— 仅快速前进,工作树有未提交改动 /
本地历史分叉一律拒绝(绝不 stash / 覆盖)。更新成功后在 API 层置 ``restart_requested``
→ worker 以退出码 81 退出 → parent 监督器拉全新 worker;editable 安装下工作树即源码,
新进程 import 即新代码。网络仅由用户显式触发(检查/应用按钮),后台永不自动。

``check_update`` / ``apply_update`` 接受可注入的 ``root``(缺省 = 仓库根,与
routes._FRONTEND_DIST 同源),测试用临时 git 仓库即本地 bare origin,无网络依赖。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_MAIN = "origin/main"
_FETCH_TIMEOUT = 30.0  # 网络拉取超时(秒)
_NO_TAG = "未打标签"

# 非交互:远端要凭据/提示时静默失败,绝不挂起等待用户输入。
_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"}


class UpdateError(Exception):
    """更新被拒/失败(工作树脏、历史分叉、网络失败、git 缺失)。detail 即给用户的文案。"""


@dataclass(frozen=True)
class UpdateStatus:
    """检查结果快照(API 层 asdict 直出)。ok=False 时仅 current_version 有意义。"""

    ok: bool = False  # git 可用且远端可达(其余字段才可信)
    error: str | None = None  # ok=False 时的人类可读原因
    current_version: str = ""  # HEAD 最近可达标签;无标签 → "未打标签"
    current_sha: str = ""  # 本地 HEAD 短 SHA
    latest_version: str | None = None  # origin 最新标签;远端无标签 → None
    latest_sha: str | None = None  # origin/main 短 SHA
    up_to_date: bool = False  # HEAD 已含 origin/main 全部提交
    available: bool = False  # 可 ff-only 更新(干净 + 未分叉 + 远端领先)
    dirty: bool = False  # 工作树有未提交改动(更新将被拒)
    conflicted: bool = False  # 本地与远端历史分叉(无法 ff-only)
    commits_behind: int = 0  # HEAD..origin/main 提交数


@dataclass
class _RepoState:
    """一次检查收集的原始事实,派生 ok/available/conflicted/up_to_date。"""

    dirty: bool = False
    current_version: str = ""
    current_sha: str = ""
    latest_version: str | None = None
    latest_sha: str | None = None
    behind: int = 0
    diverged: bool = False
    errors: list[str] = field(default_factory=list)

    def to_status(self) -> UpdateStatus:
        ok = not self.errors
        clean = not self.dirty and not self.diverged
        return UpdateStatus(
            ok=ok,
            error="; ".join(self.errors) if self.errors else None,
            current_version=self.current_version,
            current_sha=self.current_sha,
            latest_version=self.latest_version,
            latest_sha=self.latest_sha,
            up_to_date=ok and self.behind == 0 and not self.diverged,
            available=ok and clean and self.behind > 0,
            dirty=self.dirty,
            conflicted=self.diverged,
            commits_behind=self.behind,
        )


def _git(root: Path, args: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    """在仓库根跑 git(静默窗口 + 非交互),check=False 由调用方判 rc。
    Windows 下隐藏子进程控制台窗口(worker 由 VBS 静默拉起,禁止弹窗)。"""
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_GIT_ENV,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _git_or_error(
    root: Path, args: list[str], *, timeout: float = 10.0
) -> subprocess.CompletedProcess:
    """跑 git;git 缺失或超时 → UpdateError。rc 非 0 不抛,由调用方处理。"""
    if shutil.which("git") is None:
        raise UpdateError("系统未安装 git,无法自更新")
    try:
        return _git(root, args, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise UpdateError(f"git 命令超时:{e.cmd}") from e


def _short(sha: str) -> str:
    return sha[:7] if sha else ""


def _strip(line: str) -> str:
    return line.rstrip("\n")


def _is_git_repo(root: Path) -> bool:
    r = _git_or_error(root, ["rev-parse", "--is-inside-work-tree"], timeout=5.0)
    return r.returncode == 0 and r.stdout.strip() == "true"


def _describe_tag(root: Path, ref: str = "HEAD") -> str | None:
    """ref 最近可达标签;无 → None。git describe 对 ref 无对象时返回码非 0。"""
    r = _git_or_error(root, ["describe", "--tags", "--abbrev=0", ref], timeout=5.0)
    return _strip(r.stdout) if r.returncode == 0 else None


def _gather(root: Path) -> _RepoState:
    """收集一次检查的事实:本地身份/干净度 → fetch(更新 origin/main,不动工作树)
    → 与远端对比。任何一步失败记入 errors(其余字段继续收集)。"""
    st = _RepoState()
    try:
        if not _is_git_repo(root):
            st.errors.append("非 git 仓库,无法自更新")
            return st
    except UpdateError as e:
        st.errors.append(str(e))
        return st

    try:
        st.current_version = _describe_tag(root) or _NO_TAG
        st.current_sha = _short(
            _strip(_git_or_error(root, ["rev-parse", "--short", "HEAD"]).stdout)
        )
        r = _git_or_error(root, ["status", "--porcelain"], timeout=5.0)
        st.dirty = bool(r.stdout.strip())
    except UpdateError as e:
        st.errors.append(str(e))

    try:
        r = _git_or_error(root, ["fetch", "origin"], timeout=_FETCH_TIMEOUT)
        if r.returncode != 0:
            st.errors.append(f"拉取远端失败:{_strip(r.stderr) or _strip(r.stdout) or '未知错误'}")
            return st
        remote = _strip(_git_or_error(root, ["rev-parse", "origin/main"]).stdout)
        if not remote:
            st.errors.append("远端无 main 分支")
            return st
        st.latest_sha = _short(remote)
        # 最新版本 = origin/main 最近可达标签(fetch 后的本地 tag 即远端随行标签;
        # 反映"更新后实际拿到的版本",比 ls-remote 版本排序更贴实际)
        st.latest_version = _describe_tag(root, "origin/main")
        head = _strip(_git_or_error(root, ["rev-parse", "HEAD"]).stdout)
        if head == remote:
            st.behind = 0
            st.diverged = False
        else:
            count = _git_or_error(root, ["rev-list", "--count", "HEAD..origin/main"], timeout=5.0)
            st.behind = int(count.stdout.strip() or 0)
            ancestor = _git_or_error(
                root, ["merge-base", "--is-ancestor", "HEAD", "origin/main"], timeout=5.0
            )
            st.diverged = ancestor.returncode != 0
    except (UpdateError, ValueError):
        st.errors.append("远端比较失败")

    return st


def check_update(root: Path | None = None) -> UpdateStatus:
    """检查更新:git 可用性 + 工作树干净度 + fetch(更新 origin/main,不动工作树)+ 版本对比。"""
    return _gather(root or _PROJECT_ROOT).to_status()


def apply_update(root: Path | None = None) -> str:
    """拉取并更新到 origin/main(严格 ff-only)。

    * 工作树脏 / 本地分叉 → UpdateError(绝不 stash/覆盖);
    * 拉取(网络失败 → UpdateError)→ ff-only 合并(失败 → UpdateError);
    * 返回更新后的 HEAD 短 SHA。API 层据此置 restart_requested 触发重启。
    """
    root = root or _PROJECT_ROOT
    st = _gather(root)
    if st.errors:
        raise UpdateError("; ".join(st.errors))
    if st.dirty:
        raise UpdateError("工作树有未提交改动,更新前请先提交或还原(git status 查看)")
    if st.diverged:
        raise UpdateError("本地与远端历史分叉,无法快速前进更新,需手动处理(git rebase / merge)")
    if st.behind == 0:
        return st.current_sha

    r = _git_or_error(root, ["fetch", "origin"], timeout=_FETCH_TIMEOUT)
    if r.returncode != 0:
        raise UpdateError(f"拉取远端失败:{_strip(r.stderr) or _strip(r.stdout) or '未知错误'}")
    r = _git_or_error(root, ["merge", "--ff-only", _MAIN], timeout=_FETCH_TIMEOUT)
    if r.returncode != 0:
        raise UpdateError(f"更新合并失败:{_strip(r.stderr) or _strip(r.stdout) or '未知错误'}")
    new_sha = _strip(_git_or_error(root, ["rev-parse", "--short", "HEAD"]).stdout)
    return new_sha
