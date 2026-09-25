#!/usr/bin/env python3
"""一条命令把「生成 -> 静态验收 -> 干净克隆真启动 -> 推 GitHub」跑到底。

存在的理由：整条链路默认要跑到「仓库已经在 GitHub 上」才算完，中途不该把活交回给人。
本脚本把四步串起来，每步实时打印进度行，最后给一份汇总 JSON。
任何一步失败默认立刻停下（要硬撑着跑完剩下的 spec 才加 --keep-going）。

用法（多份 spec 一次跑完，最后一步真的推）：

    python3 scripts/run_pipeline.py \
      --parent "/path/to/父目录" \
      --spec /path/to/a.json --spec /path/to/b.json \
      --publish --confirm PUBLISH

不加 --publish 时只生成 + 验收，不碰远端（本地预演用）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent

SCAFFOLD = SCRIPT_DIR / "scaffold_repo.py"
VERIFY = SCRIPT_DIR / "verify_repo.py"
PROBE = SCRIPT_DIR / "probe_startup.py"
PUBLISH = SCRIPT_DIR / "publish_github.py"


class StepFailed(RuntimeError):
    """某一步没通过：带上可读原因，交给上层决定是停还是继续。"""

    def __init__(self, step: str, detail: str):
        super().__init__(f"{step} 未通过：{detail}")
        self.step = step
        self.detail = detail


def _extract_json(text: str) -> dict:
    """脚本的 stdout 里可能先打几行人读信息，JSON 在最后一个 '{' 起的那段。"""
    for idx, line in enumerate(text.splitlines()):
        if line.lstrip().startswith("{"):
            tail = "\n".join(text.splitlines()[idx:])
            try:
                return json.loads(tail)
            except json.JSONDecodeError:
                continue
    raise StepFailed("解析输出", f"拿不到 JSON：{text[-400:]}")


def _run(step: str, cmd: list[str]) -> dict:
    """跑一个子脚本，拿它的 JSON 报告；退出码非 0 就抛 StepFailed。"""
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    combined = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    try:
        payload = _extract_json(combined)
    except StepFailed:
        if result.returncode != 0:
            raise StepFailed(step, (result.stderr or result.stdout or "").strip()[-400:])
        raise
    if result.returncode != 0 or not payload.get("ok", False):
        if "ok" not in payload:
            raise StepFailed(step, "子脚本输出里没有 ok 字段："
                                   + json.dumps(payload, ensure_ascii=False)[:300])
        reason = payload.get("reason") or payload.get("failures") or \
            [s for s in payload.get("steps", []) if not s.get("ok")] or "退出码非 0"
        raise StepFailed(step, json.dumps(reason, ensure_ascii=False)[:400])
    return payload


def run_one(spec_path: Path, args: argparse.Namespace, index: int, total: int) -> dict:
    tag = f"[{index}/{total}]"
    record: dict[str, object] = {"spec": str(spec_path), "steps": {}}

    def log(step: str, detail: str) -> None:
        print(f"{tag} {step} ... {detail}", flush=True)

    # 1) 生成
    cmd = [sys.executable, str(SCAFFOLD), "--parent", str(args.parent), "--spec", str(spec_path)]
    if args.prefix:
        cmd += ["--prefix", args.prefix]
    if args.frontend:
        cmd += ["--frontend", args.frontend]
    if args.backend:
        cmd += ["--backend", args.backend]
    if args.owner:
        cmd += ["--owner", args.owner]
    if args.no_github_check:
        cmd += ["--no-github-check"]
    cmd.append("--git-init")

    scaffold = _run("生成", cmd)
    repo_root = Path(scaffold["repo_root"])
    record.update({
        "project_id": scaffold["project"]["project_id"],
        "container": scaffold["project"].get("container"),
        "repo_root": str(repo_root),
        "app_name": scaffold["app_name"],
        "modules": scaffold["modules"],
        "lines_written": scaffold["lines_written"],
    })
    record["steps"]["生成"] = True
    log("生成", f"{record['project_id']} {record['app_name']} -> {repo_root}")

    # 2) 静态验收
    verify = _run("静态验收", [sys.executable, str(VERIFY), "--repo", str(repo_root), "--json"])
    capacity = (verify.get("checks", {}).get("capacity") or {})
    record["steps"]["静态验收"] = True
    record["capacity"] = capacity.get("capacity")
    record["subject_count"] = capacity.get("subject_count")
    record["code_lines"] = (verify.get("checks", {}).get("code_size") or {}).get("code_lines")
    log("静态验收", f"通过（出题容量 {record['capacity']} 条 / 主体 {record['subject_count']} 个 / "
                    f"{record['code_lines']} 行）")

    # 3) 干净克隆真启动
    if args.skip_probe:
        record["steps"]["真启动验收"] = "skipped"
        log("真启动验收", "已按 --skip-probe 跳过")
    else:
        probe_cmd = [sys.executable, str(PROBE), "--repo", str(repo_root), "--clean-clone", "--build"]
        if args.skip_install:
            probe_cmd.append("--skip-install")
        if args.timeout:
            probe_cmd += ["--timeout", str(args.timeout)]
        probe = _run("真启动验收", probe_cmd)
        record["steps"]["真启动验收"] = True
        record["cleaned_up"] = probe.get("cleaned_up")
        log("真启动验收", f"通过（进程清理 {probe.get('cleaned_up')}）")

    # 4) 推 GitHub
    if not args.publish:
        record["steps"]["推送 GitHub"] = "skipped"
        log("推送 GitHub", "未加 --publish，只做本地预演")
        record["ok"] = True
        return record

    publish_cmd = [sys.executable, str(PUBLISH), "--repo", str(repo_root),
                   "--visibility", args.visibility, "--json",
                   "--execute", "--confirm", "PUBLISH"]
    if args.owner:
        publish_cmd += ["--owner", args.owner]
    if args.remote_url:
        publish_cmd += ["--remote-url", args.remote_url]
    published = _run("推送 GitHub", publish_cmd)
    record["steps"]["推送 GitHub"] = True
    record.update({
        "repo_url": published.get("repo_url"),
        "visibility_after": published.get("visibility_after"),
        "commit": published.get("commit"),
        "remote_head_after": published.get("remote_head_after"),
        "pushed": published.get("pushed"),
    })
    same = published.get("commit") and published.get("commit") == published.get("remote_head_after")
    record["remote_matches_local"] = bool(same)
    log("推送 GitHub", f"{record.get('repo_url')} 可见性 {record.get('visibility_after')} "
                       f"远端=本地 {record['remote_matches_local']}")

    record["ok"] = bool(
        record["steps"]["生成"] is True
        and record["steps"]["静态验收"] is True
        and record["steps"]["真启动验收"] in (True, "skipped")
        and record["steps"]["推送 GitHub"] is True
        # 非 GitHub 远端（本地裸仓库 / GitLab）没有可见性概念，不参与判定
        and (args.remote_url or record.get("visibility_after") == args.visibility)
        and record["remote_matches_local"]
    )
    return record


def main() -> int:
    parser = argparse.ArgumentParser(
        description="一条命令跑完「生成 -> 静态验收 -> 干净克隆真启动 -> 推 GitHub」")
    parser.add_argument("--parent", required=True, help="父目录，编号从这里往后排")
    parser.add_argument("--spec", action="append", required=True,
                        help="spec json 路径，可重复传多次，按传入顺序逐个跑")
    parser.add_argument("--prefix", help="项目编号前缀，默认 cc")
    parser.add_argument("--frontend", choices=["vue", "react"])
    parser.add_argument("--backend", choices=["python", "java"])
    parser.add_argument("--owner", help="GitHub owner，默认取 gh 当前登录账号")
    parser.add_argument("--visibility", choices=["public", "private", "internal"], default="public",
                        help="仓库可见性，默认 public（下游容器要能匿名 clone）")
    parser.add_argument("--publish", action="store_true",
                        help="真的推到 GitHub；不加只做本地生成 + 验收")
    parser.add_argument("--confirm", help="配合 --publish 时必须等于 PUBLISH")
    parser.add_argument("--remote-url",
                        help="非 GitHub 远端（GitLab / 本地裸仓库）：跳过 gh 建仓直接推，"
                             "可见性校验随之跳过")
    parser.add_argument("--skip-probe", action="store_true",
                        help="跳过干净克隆真启动（只做快速通道，交付前不要用）")
    parser.add_argument("--skip-install", action="store_true", help="探针跳过装依赖")
    parser.add_argument("--timeout", type=float, help="探针每步等待上限（秒）")
    parser.add_argument("--no-github-check", action="store_true",
                        help="生成时不查 GitHub 编号占用（离线用）")
    parser.add_argument("--keep-going", action="store_true",
                        help="某个 spec 失败后继续跑剩下的；默认第一个失败就停")
    parser.add_argument("--json-out", help="把汇总 JSON 同时写到这个路径")
    args = parser.parse_args()

    if args.publish and args.confirm != "PUBLISH":
        parser.error("--publish 需要配合 --confirm PUBLISH")

    specs = [Path(s).expanduser().resolve() for s in args.spec]
    for spec in specs:
        if not spec.is_file():
            parser.error(f"spec 不存在：{spec}")

    parent = Path(args.parent).expanduser().resolve()
    parent.mkdir(parents=True, exist_ok=True)

    total = len(specs)
    repos: list[dict] = []
    stopped_early = False

    for i, spec in enumerate(specs, start=1):
        try:
            repos.append(run_one(spec, args, i, total))
        except StepFailed as exc:
            repos.append({
                "spec": str(spec),
                "ok": False,
                "failed_step": exc.step,
                "reason": exc.detail,
            })
            print(f"[{i}/{total}] 失败：{exc}", flush=True)
            if not args.keep_going:
                stopped_early = True
                break

    ok = all(r.get("ok") for r in repos) and not stopped_early and len(repos) == total
    report = {
        "ok": bool(ok),
        "parent": str(parent),
        "published": bool(args.publish),
        "specs_total": total,
        "specs_done": len(repos),
        "stopped_early": stopped_early,
        "repos": repos,
    }

    print()
    print(f"=== 汇总：{sum(1 for r in repos if r.get('ok'))}/{total} 通过"
          f"{'，已推到 GitHub' if args.publish else '，未推送（本地预演）'} ===")
    for r in repos:
        state = "通过" if r.get("ok") else f"未通过（{r.get('failed_step', '?')}）"
        print(f"  {r.get('project_id') or Path(r['spec']).stem:<10} {state:<18} "
              f"{r.get('app_name') or ''} {r.get('repo_url') or ''}")

    if args.json_out:
        Path(args.json_out).expanduser().write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
