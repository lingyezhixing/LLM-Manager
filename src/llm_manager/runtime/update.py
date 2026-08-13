"""Git 自更新编排(仅向前更新,严格 ff-only;无回退 / 无版本选择 / 无提交树浏览)。

版本以 git 标签为身份(当前 = HEAD 最近可达标签);更新目标两个细粒度:
* ``tag``    → origin/main 最近可达标签(稳定发布,推荐);
* ``commit`` → origin/main 最新提交(前沿)。

只允许向前:本地有未提交改动时不预拒,交给 git 原语判定——仅与更新内容冲突的改动
才会被拒(绝不 stash / 覆盖);历史分叉同样拒绝。**不做回退**:数据库结构只向前迁移,
旧代码无法解读新 schema,故不支持回到旧版本,也不支持任选历史提交。

网络仅由用户显式触发(检查/应用按钮),后台永不自动。``check_update`` /
``apply_update`` 接受可注入的 ``root``(缺省 = 仓库根),测试用临时 git 仓库即
本地 bare origin,无网络依赖。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_FETCH_TIMEOUT = 30.0  # 网络拉取超时(秒)
_NO_TAG = "未打标签"

# 非交互:远端要凭据/提示时静默失败,绝不挂起等待用户输入。
_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"}

_TARGETS = ("commit", "tag")


class UpdateError(Exception):
    """更新被拒/失败(目标解析失败、历史分叉、冲突、网络失败、git 缺失)。detail 即给用户的文案。"""


@dataclass(frozen=True)
class UpdateStatus:
    """检查结果快照(API 层 asdict 直出)。ok=False 时仅 current_* 有意义。"""

    ok: bool = False  # git 可用且远端可达(其余字段才可信)
    error: str | None = None  # ok=False 时的人类可读原因
    current_version: str = ""  # HEAD 最近可达标签;无标签 → "未打标签"
    current_sha: str = ""  # 本地 HEAD 短 SHA
    dirty: bool = False  # 工作树有未提交改动(仅提示,不预拒)
    conflicted: bool = False  # 本地与远端历史分叉(两个目标均不可用)
    tag: str | None = None  # 最新标签名;远端无标签 → None
    tag_sha: str | None = None
    tag_available: bool = False  # 可 ff-only 更新到该标签
    tag_behind: int = 0  # HEAD..tag 提交数(落后数,仅展示)
    commit_sha: str | None = None  # origin/main 最新提交短 SHA
    commit_available: bool = False  # 可 ff-only 更新到该提交
    commit_behind: int = 0  # HEAD..origin/main 提交数(落后数,仅展示)


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


def _strip(line: str) -> str:
    return line.rstrip("\n")


def _short(sha: str) -> str:
    return sha[:7] if sha else ""


def _is_git_repo(root: Path) -> bool:
    r = _git_or_error(root, ["rev-parse", "--is-inside-work-tree"], timeout=5.0)
    return r.returncode == 0 and r.stdout.strip() == "true"


def _describe_tag(root: Path, ref: str = "HEAD") -> str | None:
    """ref 最近可达标签;无 → None。"""
    r = _git_or_error(root, ["describe", "--tags", "--abbrev=0", ref], timeout=5.0)
    return _strip(r.stdout) if r.returncode == 0 else None


def _full(root: Path, ref: str) -> str:
    """ref 完整 SHA;不存在 → ""。"""
    r = _git_or_error(root, ["rev-parse", ref], timeout=5.0)
    return _strip(r.stdout) if r.returncode == 0 else ""


def _is_ancestor(root: Path, a: str, b: str) -> bool:
    return _git_or_error(root, ["merge-base", "--is-ancestor", a, b], timeout=5.0).returncode == 0


def _behind(root: Path, range_: str) -> int:
    """range_ 左侧有而右侧没有的提交数(HEAD..ref 的落后数)。"""
    r = _git_or_error(root, ["rev-list", "--count", range_], timeout=5.0)
    return int(r.stdout.strip() or 0)


def _merge_failure_hint(root: Path, proc: subprocess.CompletedProcess) -> str:
    """merge 失败归因:冲突(本地改动被覆盖)→ 提示清理;非 ff(分叉)→ 提示手动处理;否则通用。"""
    dirty = bool(_git_or_error(root, ["status", "--porcelain"], timeout=5.0).stdout.strip())
    msg = _strip(proc.stderr) or _strip(proc.stdout) or "未知错误"
    if dirty:
        return "本地有未提交改动与更新冲突,请先提交或还原(git status 查看)"
    if "fast-forward" in msg.lower():
        return "本地与远端历史分叉,无法快速前进更新,需手动处理(git rebase / merge)"
    return f"更新合并失败:{msg}"


def check_update(root: Path | None = None) -> UpdateStatus:
    """检查更新:git 可用性 + 工作树干净度 + fetch(更新 origin/main,不动工作树)+ 两目标可用性。"""
    root = root or _PROJECT_ROOT
    current_version = ""
    current_sha = ""
    try:
        if not _is_git_repo(root):
            return UpdateStatus(ok=False, error="非 git 仓库,无法自更新")
        current_version = _describe_tag(root) or _NO_TAG
        current_sha = _short(_full(root, "HEAD"))
        dirty = bool(_git_or_error(root, ["status", "--porcelain"], timeout=5.0).stdout.strip())
    except UpdateError as e:
        return UpdateStatus(
            ok=False, error=str(e), current_version=current_version, current_sha=current_sha
        )

    try:
        r = _git_or_error(root, ["fetch", "origin"], timeout=_FETCH_TIMEOUT)
        if r.returncode != 0:
            return UpdateStatus(
                ok=False,
                error=f"拉取远端失败:{_strip(r.stderr) or _strip(r.stdout) or '未知错误'}",
                current_version=current_version,
                current_sha=current_sha,
                dirty=dirty,
            )
        remote = _full(root, "origin/main")
        head = _full(root, "HEAD")
        if not remote:
            return UpdateStatus(
                ok=False,
                error="远端无 main 分支",
                current_version=current_version,
                current_sha=current_sha,
                dirty=dirty,
            )
        diverged = head != remote and not _is_ancestor(root, head, remote)
        # 目标可用 = 未分叉 且 目标不在 HEAD 之后(HEAD 是目标的祖先,可 ff-only 追上)
        commit_sha = _short(remote)
        commit_available = not diverged and head != remote and _is_ancestor(root, head, remote)
        commit_behind = _behind(root, "HEAD..origin/main") if head != remote else 0
        tag = _describe_tag(root, "origin/main")
        tag_full = _full(root, tag) if tag else ""
        tag_sha = _short(tag_full) if tag_full else None
        tag_available = (
            bool(tag_full)
            and not diverged
            and tag_full != head
            and _is_ancestor(root, head, tag_full)
        )
        tag_behind = _behind(root, f"HEAD..{tag}") if tag_full and tag_full != head else 0
        return UpdateStatus(
            ok=True,
            current_version=current_version,
            current_sha=current_sha,
            dirty=dirty,
            conflicted=diverged,
            tag=tag,
            tag_sha=tag_sha,
            tag_available=tag_available,
            tag_behind=tag_behind,
            commit_sha=commit_sha,
            commit_available=commit_available,
            commit_behind=commit_behind,
        )
    except UpdateError as e:
        return UpdateStatus(
            ok=False,
            error=str(e),
            current_version=current_version,
            current_sha=current_sha,
            dirty=dirty,
        )


def apply_update(root: Path | None = None, *, target: str = "commit") -> str:
    """拉取并向前更新到目标细粒度(commit=origin/main 最新提交 / tag=最新标签)。

    * 目标解析失败(无标签等)→ UpdateError;
    * 历史分叉 / 本地改动与更新冲突 → UpdateError(只 ff-only,绝不 stash / 覆盖 / 回退);
    * 返回更新后的 HEAD 短 SHA。API 层据此置 restart_requested 触发重启。
    """
    root = root or _PROJECT_ROOT
    if target not in _TARGETS:
        raise UpdateError("未知更新目标(仅支持 commit / tag)")
    r = _git_or_error(root, ["fetch", "origin"], timeout=_FETCH_TIMEOUT)
    if r.returncode != 0:
        raise UpdateError(f"拉取远端失败:{_strip(r.stderr) or _strip(r.stdout) or '未知错误'}")

    if target == "tag":
        tag = _describe_tag(root, "origin/main")
        if tag is None:
            raise UpdateError("远端无标签可更新(最新提交仍可用)")
        ref = tag
    else:
        ref = "origin/main"

    merge = _git_or_error(root, ["merge", "--ff-only", ref], timeout=_FETCH_TIMEOUT)
    if merge.returncode != 0:
        raise UpdateError(_merge_failure_hint(root, merge))
    return _short(_full(root, "HEAD"))
