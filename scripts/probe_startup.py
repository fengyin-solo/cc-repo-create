#!/usr/bin/env python3
"""真起一遍：后端起服务 + 前端起 dev server，curl 到页面与接口，再全部停掉。

只做三件事，不改仓库里的业务代码：

  1. 装依赖（前端 npm install，后端建 venv 装 requirements.txt；已有依赖就跳过）。
  2. 起后端，轮询 /api/health；起前端，轮询首页 HTML；再从前端端口走一次 /api/health，
     证明 vite 代理到后端的链路通。
  3. 无论成功失败，把起过的进程全部停掉（进程组整组杀），把证据写成 JSON。

用法：

    python3 probe_startup.py --repo /path/to/repo
    python3 probe_startup.py --repo /path/to/repo --skip-install
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def free_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def http_get(url: str, timeout: float = 3.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")
    except Exception as error:  # noqa: BLE001 - 连接未就绪时各种异常都算「还没起来」
        return 0, str(error)


def wait_for(check, *, timeout: float, interval: float = 0.5) -> tuple[bool, str]:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        ok, detail = check()
        last = detail
        if ok:
            return True, detail
        time.sleep(interval)
    return False, last


def terminate(process: subprocess.Popen, log_path: Path) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        process.wait(timeout=10)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\n== 探针已停止该进程 ==\n")


def spawn(command: list[str], cwd: Path, log_path: Path, env: dict[str, str] | None = None):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("w", encoding="utf-8")
    environment = {**os.environ, **(env or {})}
    environment.setdefault("BROWSER", "none")
    process = subprocess.Popen(
        command, cwd=cwd, stdout=handle, stderr=subprocess.STDOUT,
        start_new_session=True, env=environment,
    )
    return process, handle


def detect_backend(repo: Path) -> dict[str, object]:
    backend = next((repo / name for name in ("backend", "server", "api") if (repo / name).is_dir()), None)
    if backend is None:
        return {"kind": "missing"}
    if (backend / "pom.xml").is_file() or (backend / "build.gradle").is_file():
        return {"kind": "java", "dir": backend}
    if (backend / "requirements.txt").is_file() or (backend / "app" / "main.py").is_file():
        return {"kind": "python", "dir": backend}
    return {"kind": "unknown", "dir": backend}


def detect_frontend(repo: Path) -> Path | None:
    for name in ("frontend", "web", "frontend-portal"):
        candidate = repo / name
        if (candidate / "package.json").is_file():
            return candidate
    for candidate in sorted(repo.iterdir()):
        if candidate.is_dir() and candidate.name.startswith(("frontend", "web")):
            if (candidate / "package.json").is_file():
                return candidate
    return None


def detect_ports(repo: Path, backend_dir: Path | None, frontend_dir: Path | None) -> tuple[int, int]:
    backend_port, frontend_port = 8000, 5173
    if backend_dir:
        for candidate in (backend_dir / "app" / "config.py",
                          backend_dir / "src" / "main" / "resources" / "application.yml",
                          backend_dir / "src" / "main" / "resources" / "application.properties"):
            if not candidate.is_file():
                continue
            match = re.search(r"port\D{0,8}(\d{2,5})", candidate.read_text(encoding="utf-8", errors="replace"))
            if match:
                backend_port = int(match.group(1))
                break
    if frontend_dir:
        vite = frontend_dir / "vite.config.ts"
        if vite.is_file():
            match = re.search(r"port\s*:\s*(\d{2,5})", vite.read_text(encoding="utf-8", errors="replace"))
            if match:
                frontend_port = int(match.group(1))
    return backend_port, frontend_port


def discover_business_endpoints(backend_dir: Path | None) -> list[str]:
    """从仓库里找出可探的业务接口，避免把某个题域的模块名写死。

    先扫 Python 后端的 app/routers/*.py 里的 APIRouter(prefix="/api/xxx")，
    再扫 Java 后端的 @RequestMapping("/api/xxx")；都扫不到时退回 /api/visitor
    （默认 spec 的模块），保证老仓库的行为不变。
    """
    found: list[str] = []
    if backend_dir:
        routers = backend_dir / "app" / "routers"
        if routers.is_dir():
            for path in sorted(routers.glob("*.py")):
                if path.stem == "__init__":
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                for match in re.findall(r"prefix\s*=\s*[\"']([^\"']+)[\"']", text):
                    if match not in found:
                        found.append(match)
        java_root = backend_dir / "src" / "main" / "java"
        if java_root.is_dir():
            for path in sorted(java_root.rglob("*.java")):
                text = path.read_text(encoding="utf-8", errors="replace")
                for match in re.findall(r"@RequestMapping\(\s*[\"']([^\"']+)[\"']", text):
                    if match not in found:
                        found.append(match)
    return found or ["/api/visitor"]


def run_clean_clone(repo: Path, args) -> int:
    """在干净克隆里跑完整探针：证明仓库不依赖任何未提交的本地残留。

    这是「基础环境有没有问题」最硬的一条证据：把仓库 clone 到临时目录后，
    依赖目录都不会跟过来（被 .gitignore 挡住了），必须现场装一遍才能起来。
    """
    report: dict[str, object] = {"repo": str(repo), "mode": "clean-clone", "steps": []}
    ok = True

    def record(name: str, passed: bool, detail: str, **extra: object) -> None:
        nonlocal ok
        report["steps"].append({"step": name, "ok": passed, "detail": detail, **extra})
        if not passed:
            ok = False

    if not (repo / ".git").exists():
        print(json.dumps({
            **report,
            "ok": False,
            "failed_steps": [{"step": "clean-clone", "ok": False,
                              "detail": "仓库还不是 git 仓库，clean-clone 需要先 git init + 提交"}],
        }, ensure_ascii=False, indent=2))
        return 2

    workspace = Path(tempfile.mkdtemp(prefix="repo-clean-clone-"))
    clone = workspace / repo.name
    clone_proc = subprocess.run(["git", "clone", "--quiet", str(repo), str(clone)],
                                capture_output=True, text=True)
    record("克隆仓库到干净目录", clone_proc.returncode == 0,
           (clone_proc.stderr or clone_proc.stdout or "ok").strip()[-300:], clone_dir=str(clone))
    if clone_proc.returncode != 0:
        print(json.dumps({**report, "ok": False,
                          "failed_steps": report["steps"]}, ensure_ascii=False, indent=2))
        return 1

    status = subprocess.run(["git", "status", "--porcelain"], cwd=clone,
                            capture_output=True, text=True)
    record("克隆出来的工作区是干净的", status.stdout.strip() == "",
           status.stdout.strip()[:200] or "工作区干净，没有未提交的必需文件")

    carried = [str(path.relative_to(clone)) for name in ("node_modules", ".venv", "dist", "build")
               for path in clone.rglob(name) if path.is_dir()]
    record("依赖目录没有被提交进仓库", not carried,
           "、".join(carried[:5]) or "node_modules / .venv / dist / build 都没进仓库",
           carried=carried[:10])

    command = [sys.executable, str(Path(__file__).resolve()), "--repo", str(clone),
               "--timeout", str(args.timeout)]
    if args.build:
        command.append("--build")
    probe = subprocess.run(command, capture_output=True, text=True)
    try:
        payload = json.loads(probe.stdout[probe.stdout.index("{"):])
    except (ValueError, json.JSONDecodeError):
        payload = {"ok": False, "steps": [],
                   "raw_tail": (probe.stdout or probe.stderr)[-400:]}
    for step in payload.get("steps", []):
        report["steps"].append({**step, "step": f"克隆内：{step['step']}"})
    record("克隆里能装依赖、起服务、打开页面", bool(payload.get("ok")),
           "全部通过" if payload.get("ok") else "见上面失败步骤")
    report["clean_clone_probe"] = payload
    report["clone_dir"] = str(clone)
    # 克隆内那一轮的「进程是否停干净」要透出来，外层报告才完整
    report["cleaned_up"] = payload.get("cleaned_up")
    report["ok"] = ok
    report["failed_steps"] = [step for step in report["steps"] if not step["ok"]]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="真起一遍前端与后端，并验证页面能打开")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--timeout", type=float, default=180.0, help="每一步的等待上限（秒）")
    parser.add_argument("--skip-install", action="store_true", help="跳过装依赖（依赖已就绪时用）")
    parser.add_argument("--logs", help="日志目录，默认写到仓库外层的 .probe-logs")
    parser.add_argument("--clean-clone", action="store_true",
                        help="先把仓库 clone 到一个干净临时目录，再在克隆里跑完整探针"
                             "（证明不依赖任何未提交的本地残留）")
    parser.add_argument("--build", action="store_true",
                        help="探针跑完再在克隆里跑一次前端 npm run build")
    args = parser.parse_args()

    repo = Path(args.repo).expanduser().resolve()

    if args.clean_clone:
        return run_clean_clone(repo, args)

    logs = Path(args.logs).expanduser().resolve() if args.logs else repo.parent / ".probe-logs" / repo.name
    logs.mkdir(parents=True, exist_ok=True)

    backend = detect_backend(repo)
    frontend_dir = detect_frontend(repo)
    backend_dir = backend.get("dir")
    backend_port, frontend_port = detect_ports(repo, backend_dir, frontend_dir)

    steps: list[dict[str, object]] = []
    processes: list[tuple[subprocess.Popen, Path]] = []
    result: dict[str, object] = {
        "repo": str(repo),
        "backend": str(backend.get("kind")),
        "backend_dir": str(backend_dir) if backend_dir else "",
        "frontend_dir": str(frontend_dir) if frontend_dir else "",
        "ports": {"backend": backend_port, "frontend": frontend_port},
        "steps": steps,
        "logs_dir": str(logs),
    }
    ok = True

    def record(name: str, passed: bool, detail: str, **extra: object) -> None:
        nonlocal ok
        steps.append({"step": name, "ok": passed, "detail": detail, **extra})
        if not passed:
            ok = False

    live_backend_port: int | None = None
    live_frontend_port: int | None = None
    try:
        # ---------- 后端 ----------
        if backend["kind"] == "python":
            venv = backend_dir / ".venv"
            python_bin = venv / "bin" / "python"
            if not python_bin.exists() and not args.skip_install:
                proc = subprocess.run([sys.executable, "-m", "venv", str(venv)],
                                      capture_output=True, text=True)
                record("后端创建虚拟环境", proc.returncode == 0, proc.stderr[-400:] or "ok")
                pip = subprocess.run(
                    [str(venv / "bin" / "pip"), "install", "-q", "-r", "requirements.txt"],
                    cwd=backend_dir, capture_output=True, text=True)
                pip_tail = "\n".join((pip.stderr or pip.stdout).strip().splitlines()[-3:])
                record("后端安装依赖", pip.returncode == 0,
                       pip_tail or "ok")
            if not python_bin.exists():
                record("后端依赖就绪", False, f"{python_bin} 不存在，请先装依赖或去掉 --skip-install")
            else:
                port = free_port(backend_port)
                process, _ = spawn(
                    [str(python_bin), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                     "--port", str(port)],
                    backend_dir, logs / "backend.log")
                processes.append((process, logs / "backend.log"))
                base = f"http://127.0.0.1:{port}"

                def backend_ready():
                    status, body = http_get(f"{base}/api/health")
                    return status == 200, f"status={status} body={body[:200]}"

                ready, detail = wait_for(backend_ready, timeout=args.timeout)
                record("后端 /api/health 就绪", ready, detail, port=port)
                if ready:
                    live_backend_port = port
                    status, body = http_get(f"{base}/api/overview")
                    record("后端 /api/overview 有数据", status == 200 and "modules" in body,
                           f"status={status} body={body[:200]}")
                    endpoints = discover_business_endpoints(backend_dir)
                    probed = ("", 0, "")
                    for endpoint in endpoints:
                        status, body = http_get(f"{base}{endpoint}")
                        probed = (endpoint, status, body)
                        if status == 200 and "items" in body:
                            break
                    endpoint, status, body = probed
                    record("后端业务接口可访问", status == 200 and "items" in body,
                           f"{endpoint} status={status} body={body[:200]}"
                           + ("" if len(endpoints) <= 1 else f"（共试了 {len(endpoints)} 个候选）"))
        elif backend["kind"] == "java":
            if shutil.which("mvn") is None or shutil.which("java") is None:
                record("Java 后端启动", False,
                       "本机没有 JDK/Maven，无法真启动；请在装好 JDK17 + Maven 的机器上重跑本探针")
            else:
                port = free_port(backend_port)
                process, _ = spawn(
                    ["mvn", "-q", "-Dspring-boot.run.arguments=--server.port=" + str(port),
                     "spring-boot:run"], backend_dir, logs / "backend.log")
                processes.append((process, logs / "backend.log"))
                base = f"http://127.0.0.1:{port}"

                def java_ready():
                    status, body = http_get(f"{base}/api/health")
                    return status == 200, f"status={status} body={body[:200]}"

                ready, detail = wait_for(java_ready, timeout=args.timeout)
                record("Java 后端 /api/health 就绪", ready, detail, port=port)
                if ready:
                    live_backend_port = port
        else:
            record("后端启动", False, f"识别不出后端类型：{backend}")

        # ---------- 前端 ----------
        if frontend_dir is None:
            record("前端启动", False, "找不到前端目录")
        else:
            if not (frontend_dir / "node_modules").is_dir() and not args.skip_install:
                npm = shutil.which("npm")
                if npm is None:
                    record("前端安装依赖", False, "本机没有 npm")
                else:
                    proc = subprocess.run([npm, "install", "--no-audit", "--no-fund"],
                                          cwd=frontend_dir, capture_output=True, text=True)
                    record("前端安装依赖", proc.returncode == 0,
                           (proc.stderr or proc.stdout)[-600:] or "ok")
            port = free_port(frontend_port)
            npm = shutil.which("npm") or "npm"
            frontend_env = {"BROWSER": "none", "CI": "1"}
            if live_backend_port:
                # 后端可能落在非默认端口上，把真实地址告诉 vite 的代理，别让链路白断
                frontend_env["VITE_PROXY_TARGET"] = f"http://127.0.0.1:{live_backend_port}"
            process, _ = spawn(
                [npm, "run", "dev", "--", "--port", str(port), "--strictPort", "--host", "127.0.0.1"],
                frontend_dir, logs / "frontend.log",
                env=frontend_env)
            processes.append((process, logs / "frontend.log"))
            base = f"http://127.0.0.1:{port}"

            def frontend_ready():
                status, body = http_get(f"{base}/")
                mount = 'id="app"' in body or 'id="root"' in body
                return (status == 200 and mount), f"status={status} mount={mount} len={len(body)}"

            ready, detail = wait_for(frontend_ready, timeout=args.timeout)
            record("前端首页可访问且有挂载点", ready, detail, port=port)
            if ready:
                live_frontend_port = port
                entry_name = "main.ts" if (frontend_dir / "src" / "main.ts").exists() else "main.tsx"
                status, entry = http_get(f"{base}/src/{entry_name}")
                record("前端入口脚本可加载", status == 200, f"status={status} len={len(entry)}")
                if live_backend_port:
                    status, body = http_get(f"{base}/api/health")
                    record("前端 -> 后端代理链路通", status == 200 and '"ok"' in body,
                           f"status={status} body={body[:200]}")
                else:
                    record("前端 -> 后端代理链路通", False, "后端没起来，代理无从验证")

        # ---------- 生产构建（可选）----------
        if args.build and frontend_dir is not None:
            npm_bin = shutil.which("npm") or "npm"
            build_proc = subprocess.run([npm_bin, "run", "build"], cwd=frontend_dir,
                                        capture_output=True, text=True)
            build_tail = "\n".join((build_proc.stderr or build_proc.stdout).strip().splitlines()[-3:])
            record("前端生产构建通过（typecheck + vite build）", build_proc.returncode == 0,
                   build_tail or "ok")
    finally:
        result["live_ports"] = {"backend": live_backend_port, "frontend": live_frontend_port}
        for process, log_path in processes:
            terminate(process, log_path)
        result["cleaned_up"] = all(process.poll() is not None for process, _ in processes)

    result["ok"] = ok
    result["failed_steps"] = [step for step in steps if not step["ok"]]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
