#!/usr/bin/env python3
"""把准备好的仓库推到远端，默认是 GitHub 的 main 分支（其它 git 远端用 --remote-url）。

默认 dry-run：只打印「会建什么仓库、会推哪个提交、会跑哪几条命令」，不碰远端。
真推要显式加 `--execute --confirm PUBLISH`。

推之前会拦这几件事（任一条不过就停，不做强推）：

  1. 不是 git 仓库 / 当前分支不是 main / 工作区有未提交改动；
  2. 静态验收没过（`verify_repo.py`，可用 --skip-verify 跳过）；
  3. 远端 main 上已经有本地没有的提交（分叉）——这种必须人工处理；
  4. 推完回读远端 SHA，和本地 HEAD 对不上就报错。

用法：

    python3 publish_github.py --repo /path/to/repo                       # 看计划
    python3 publish_github.py --repo /path/to/repo --execute --confirm PUBLISH
    python3 publish_github.py --repo /path/to/repo --owner my-org --name my-repo --visibility private \
        --execute --confirm PUBLISH
    python3 publish_github.py --repo /path/to/repo --remote-url git@gitlab.example.com:group/repo.git \
        --execute --confirm PUBLISH

token 只以一次性 `git -c http.extraHeader=...` 传参，不写进 .git/config，也不打印。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import verify_repo as verifier  # noqa: E402
import repo_rules as rules  # noqa: E402

DEFAULT_BRANCH = "main"


def run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True)


def redact(command: list[str]) -> list[str]:
    """把命令行里的 token 洗掉再打印。"""
    safe: list[str] = []
    for item in command:
        if "Authorization: Basic" in item:
            safe.append("http.https://github.com/.extraHeader=Authorization: Basic ***")
        else:
            safe.append(item)
    return safe


def gh_binary() -> str:
    return shutil.which("gh") or "gh"


def github_token() -> str:
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value.strip()
    result = run([gh_binary(), "auth", "token"])
    return result.stdout.strip() if result.returncode == 0 else ""


def auth_args(url: str) -> list[str]:
    """远端是 github.com 时，用一次性 header 认证；其它远端交给本机 git 凭据。"""
    if "github.com" not in url:
        return []
    token = github_token()
    if not token:
        return []
    encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return [
        "-c", "credential.helper=",
        "-c", f"http.https://github.com/.extraHeader=Authorization: Basic {encoded}",
    ]


def git(args: list[str], *, cwd: Path, url: str = "") -> subprocess.CompletedProcess[str]:
    result = run(["git", *args], cwd=cwd)
    if result.returncode == 0 or not url:
        return result
    extra = auth_args(url)
    if not extra:
        return result
    return run(["git", *extra, *args], cwd=cwd)


def local_head(repo: Path, branch: str) -> str:
    result = run(["git", "rev-parse", branch], cwd=repo)
    if result.returncode != 0:
        raise SystemExit(f"本地没有分支 {branch}：{result.stderr.strip()}")
    return result.stdout.strip()


def remote_head(url: str, branch: str, repo: Path, *, allow_missing: bool = False) -> str:
    result = git(["ls-remote", url, f"refs/heads/{branch}"], cwd=repo, url=url)
    if result.returncode != 0:
        if allow_missing:
            return ""
        message = result.stderr.strip() or result.stdout.strip() or "ls-remote failed"
        raise SystemExit(f"读不到远端分支列表：{message}")
    for line in result.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.strip() == f"refs/heads/{branch}":
            return sha.strip()
    return ""


def repo_exists(full_name: str) -> bool:
    return run([gh_binary(), "repo", "view", full_name]).returncode == 0


def gh_repo_visibility(full_name: str) -> str | None:
    """读远端仓库的真实可见性；读不到（网络/权限）返回 None。"""
    result = run([gh_binary(), "repo", "view", full_name, "--json", "visibility"])
    if result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout).get("visibility") or ""
    except json.JSONDecodeError:
        return None
    return str(value).strip().lower() or None


def edit_repo_visibility(full_name: str, visibility: str) -> subprocess.CompletedProcess[str]:
    return run([gh_binary(), "repo", "edit", full_name, "--visibility", visibility,
                "--accept-visibility-change-consequences"])


def ensure_visibility(full_name: str, visibility: str, *, execute: bool) -> dict[str, object]:
    """核对远端仓库可见性，必要时改成要求的值。

    生成的仓库必须是公开的：下游容器要能匿名 clone，private 会直接卡住出题链路。
    改动不做无声操作——前后值都写进返回值，`will_fix` 表示这一轮只计划、没执行。
    """
    result: dict[str, object] = {"problems": [], "will_fix": False, "fixed": False}
    before = gh_repo_visibility(full_name)
    result["before"] = before
    if before is None:
        result["after"] = None
        return result
    if before == visibility:
        result["after"] = before
        return result

    result["planned_command"] = ["gh", "repo", "edit", full_name, "--visibility", visibility,
                                 "--accept-visibility-change-consequences"]
    if not execute:
        result["after"] = visibility
        result["will_fix"] = True
        return result

    edited = edit_repo_visibility(full_name, visibility)
    if edited.returncode != 0:
        message = edited.stderr.strip() or edited.stdout.strip() or "gh repo edit failed"
        result["after"] = gh_repo_visibility(full_name)
        result["problems"].append(f"把 {full_name} 改成 {visibility} 失败：{message}")
        return result
    after = gh_repo_visibility(full_name)
    result["after"] = after
    # fixed 表示「现在真的是要求的可见性了」，不是「改动命令返回 0」
    result["fixed"] = after == visibility
    if after != visibility:
        result["problems"].append(
            f"{full_name} 当前可见性是 {after}，要求是 {visibility}；"
            f"手工改：gh repo edit {full_name} --visibility {visibility} "
            "--accept-visibility-change-consequences")
    return result


def resolve_owner(explicit: str | None) -> str:
    if explicit:
        return explicit
    result = run([gh_binary(), "api", "user", "-q", ".login"])
    owner = result.stdout.strip()
    if result.returncode != 0 or not owner:
        message = result.stderr.strip() or "gh api user 失败"
        raise SystemExit(f"解析不出 GitHub owner，请用 --owner 显式指定：{message}")
    return owner


def clean_https_url(full_name: str) -> str:
    return f"https://github.com/{full_name}.git"


def configure_origin(repo: Path, url: str, branch: str) -> None:
    """origin 指向不带 token 的地址，免得 token 留在 .git/config 里。"""
    run(["git", "remote", "remove", "origin"], cwd=repo)
    result = run(["git", "remote", "add", "origin", url], cwd=repo)
    if result.returncode != 0:
        raise SystemExit(f"配置 origin 失败：{result.stderr.strip()}")
    run(["git", "config", f"branch.{branch}.remote", "origin"], cwd=repo)
    run(["git", "config", f"branch.{branch}.merge", f"refs/heads/{branch}"], cwd=repo)
    run(["git", "config", "--unset-all", f"branch.{branch}.pushRemote"], cwd=repo)


def publish(args) -> dict[str, object]:
    repo = Path(args.repo).expanduser().resolve()
    branch = args.branch
    report: dict[str, object] = {
        "repo": str(repo),
        "branch": branch,
        "dry_run": not args.execute,
        "steps": [],
        "planned_commands": [],
    }
    problems: list[str] = []

    if not repo.is_dir():
        raise SystemExit(f"仓库目录不存在：{repo}")
    if not (repo / ".git").exists():
        raise SystemExit(f"还不是 git 仓库，先 git init 并提交：{repo}")

    head = local_head(repo, branch)
    report["commit"] = head

    status = run(["git", "status", "--porcelain"], cwd=repo).stdout.strip()
    if status and not args.allow_dirty:
        problems.append("工作区有未提交改动，先提交再推：" + "；".join(status.splitlines()[:5]))
    report["steps"].append({"step": "工作区干净", "ok": not status or args.allow_dirty,
                            "detail": status[:200] or "clean"})

    if not args.skip_verify:
        result = verifier.verify(repo, min_lines=2000, min_capacity=rules.MIN_CAPACITY,
                                 per_mode=rules.MAX_PER_MODE, per_subject=rules.MAX_PER_SUBJECT)
        report["steps"].append({"step": "静态验收通过", "ok": bool(result["ok"]),
                                "detail": "全部硬性要求通过" if result["ok"]
                                else "；".join(result["failures"])[:400]})
        if not result["ok"]:
            problems.append("静态验收没过，先修好再推（或显式 --skip-verify）")

    # 远端地址
    full_name = ""
    remote_ready = True
    if args.remote_url:
        url = args.remote_url
    else:
        owner = resolve_owner(args.owner)
        name = args.name or repo.name
        full_name = f"{owner}/{name}"
        url = clean_https_url(full_name)
        exists = repo_exists(full_name)
        report["remote_exists"] = exists
        remote_ready = exists
        if not exists:
            create_cmd = ["gh", "repo", "create", full_name, f"--{args.visibility}"]
            if args.description:
                create_cmd += ["--description", args.description]
            report["planned_commands"].append(redact(create_cmd))
            if args.execute:
                created = run([gh_binary(), "repo", "create", full_name, f"--{args.visibility}"]
                              + (["--description", args.description] if args.description else []))
                if created.returncode != 0:
                    message = created.stderr.strip() or created.stdout.strip() or "gh repo create failed"
                    raise SystemExit(f"创建仓库 {full_name} 失败：{message}")
                report["repo_created"] = True
                remote_ready = True
        else:
            report["repo_created"] = False

    report["remote_url"] = url
    report["repo_url"] = f"https://github.com/{full_name}" if full_name else url

    # 生成的仓库必须是公开的：下游（GSB 容器）要能匿名 clone，private 会直接卡住出题链路。
    # 所以这里核对真实可见性，和请求的不一致就改过来，并把前后值都写进报告，不做无声修改。
    if full_name and remote_ready:
        visibility = ensure_visibility(full_name, args.visibility, execute=args.execute)
        report["visibility_before"] = visibility.get("before")
        report["visibility_after"] = visibility.get("after")
        report["visibility_will_fix"] = visibility.get("will_fix")
        report["visibility_fixed"] = visibility.get("fixed")
        if visibility.get("planned_command"):
            report["planned_commands"].append(redact(visibility["planned_command"]))
        problems.extend(visibility["problems"])

    # 远端 main 与本地的关系：只允许首次推送或快进，绝不强推
    if remote_ready:
        existing_remote_head = remote_head(url, branch, repo, allow_missing=bool(args.remote_url))
    else:
        existing_remote_head = ""
    if existing_remote_head:
        if existing_remote_head == head:
            action = "up-to-date"
        else:
            ancestor = run(["git", "merge-base", "--is-ancestor", existing_remote_head, head], cwd=repo)
            if ancestor.returncode == 0:
                action = "fast-forward"
            else:
                problems.append(
                    f"远端 {branch} 上有本地没有的提交（远端 {existing_remote_head[:8]}），"
                    "本脚本不做强推，请先人工合并或确认远端可以覆盖")
                action = "diverged"
    else:
        action = "create-branch"
    report["planned_action"] = action
    report["remote_head_before"] = existing_remote_head

    push_cmd = ["git", *auth_args(url), "push", "origin", f"{branch}:{branch}"]
    report["planned_commands"].append(redact(push_cmd))

    if problems:
        report["ok"] = False
        report["problems"] = problems
        return report

    if not args.execute:
        report["ok"] = True
        report["next"] = ("python3 " + str(Path(__file__).resolve())
                          + f" --repo {repo} --execute --confirm PUBLISH")
        return report

    configure_origin(repo, url, branch)
    if action != "up-to-date":
        pushed = git(["push", "origin", f"{branch}:{branch}"], cwd=repo, url=url)
        if pushed.returncode != 0:
            message = pushed.stderr.strip() or pushed.stdout.strip() or "git push failed"
            raise SystemExit(f"推送 {branch} 失败：{message}")

    after = remote_head(url, branch, repo)
    report["remote_head_after"] = after
    if after != head:
        report["ok"] = False
        report["problems"] = [f"推完回读远端 {branch}={after[:8]}，与本地 HEAD {head[:8]} 不一致"]
        return report
    report["ok"] = True
    report["pushed"] = action != "up-to-date"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="把仓库推到 GitHub（默认 main 分支）")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--owner", help="GitHub owner，默认取 gh 当前登录账号")
    parser.add_argument("--name", help="仓库名，默认取仓库目录名")
    parser.add_argument("--visibility", choices=["public", "private", "internal"], default="public",
                        help="仓库可见性，默认 public。生成的仓库必须是公开的——"
                             "下游容器要能匿名 clone；发现远端是 private 会自动改成 public 并记录")
    parser.add_argument("--description", help="仓库描述")
    parser.add_argument("--remote-url", help="非 GitHub 远端（例如 GitLab / 本地裸仓库）：跳过 gh 建仓直接推")
    parser.add_argument("--skip-verify", action="store_true",
                        help="跳过静态验收。只有推「不是本 skill 生成的项目」的仓库时才用"
                             "（例如推 skill 自身的代码仓），推生成出来的项目一律不要加")
    parser.add_argument("--allow-dirty", action="store_true", help="允许带未提交改动推送（不建议）")
    parser.add_argument("--execute", action="store_true", help="真的执行；不加就是 dry-run")
    parser.add_argument("--confirm", help="真执行时必须等于 PUBLISH")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.execute and args.confirm != "PUBLISH":
        parser.error("真推需要 --confirm PUBLISH")

    report = publish(args)
    if not args.json:
        header = "DRY-RUN（没有碰远端）" if not args.execute else "已执行"
        print(f"[{header}] {report.get('repo_url', '')} 分支 {report.get('branch')} "
              f"提交 {str(report.get('commit', ''))[:8]} 动作 {report.get('planned_action')}")
        for step in report.get("steps", []):
            print(("  通过  " if step["ok"] else "  未通过") + f" {step['step']}：{str(step['detail'])[:120]}")
        for problem in report.get("problems", []):
            print(f"  ✗ {problem}")
        if report.get("next"):
            print(f"  下一步：{report['next']}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
