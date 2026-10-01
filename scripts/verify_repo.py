#!/usr/bin/env python3
"""验收一个代码仓库是否满足本 skill 的全部硬性要求。

只读仓库，不改任何文件。任何一项不通过都以非 0 退出码结束，并把证据打印出来。

用法：

    python3 verify_repo.py --repo /path/to/repo
    python3 verify_repo.py --repo /path/to/repo --json
    python3 verify_repo.py --rules-drift /path/to/check_repo_theme.py

检查项：

  G1 结构   前端（Vue/React）齐全；后端（Java/Python）齐全，除非是纯前端仓库
            （前端 package.json 里 repoKind = frontend-only，此时必须有本地数据层）
  G2 启动   前端 dev server 关掉了自动打开页面
  G3 忽略   根目录 .gitignore 覆盖依赖 / 缓存 / 构建产物 / 日志 / 环境文件
  G4 体量   源码行数 >= --min-lines（默认 2000）
  G5 容量   主体与需求模式算出来的容量 >= --min-capacity（默认 30）
  G6 合规   不是禁用题型仓库，无密钥 / 二进制大块 / 违规内容
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import repo_rules as rules  # noqa: E402

# 计入「代码量」的文本文件后缀；锁文件、依赖目录、构建产物另算。
CODE_SUFFIXES = {
    ".vue", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".java", ".kt",
    ".go", ".rb", ".php", ".cs", ".cpp", ".cc", ".h", ".hpp", ".rs",
    ".css", ".scss", ".less", ".html", ".sql", ".sh", ".bash",
    ".json", ".yml", ".yaml", ".toml", ".xml", ".properties", ".env",
}
LOCK_FILE_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json",
    "poetry.lock", "Pipfile.lock", "Cargo.lock", "go.sum", "composer.lock",
    "gradle.lockfile",
}
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tar",
    ".jar", ".war", ".class", ".so", ".dylib", ".dll", ".exe", ".woff", ".woff2",
    ".ttf", ".mp4", ".mov", ".sqlite", ".db", ".bin", ".lock", ".pyc",
}
MAX_TEXT_FILE_BYTES = 5 * 1024 * 1024

FRONTEND_MARKERS = {
    "vue": ("vue", "@vitejs/plugin-vue", "vue-router"),
    "react": ("react", "react-dom", "react-router-dom"),
}
BACKEND_MARKERS = {
    "python": ("fastapi", "flask", "django", "uvicorn", "requirements.txt"),
    "java": ("spring-boot", "pom.xml", "build.gradle"),
}

SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"BEGIN [A-Z ]*PRIVATE KEY", "内嵌了私钥"),
    (r"\bAKIA[0-9A-Z]{16}\b", "内嵌了 AWS Access Key"),
    (r"\bghp_[A-Za-z0-9]{20,}\b", "内嵌了 GitHub Token"),
    (r"\bsk-[A-Za-z0-9]{24,}\b", "内嵌了 API Key"),
    (r"(?i)\b(password|passwd|secret|token|apikey|api_key)\s*[:=]\s*['\"][^'\"]{8,}['\"]",
     "源码里硬编码了口令或密钥"),
)

# 内容合规：命中即判不合规，需要换题域重做。
FORBIDDEN_CONTENT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"赌博|博彩|下注|投注平台|彩票代购", "涉及赌博类业务"),
    (r"色情|裸聊|约炮", "涉及色情类业务"),
    (r"毒品|冰毒|大麻交易", "涉及毒品类业务"),
    (r"枪支|弹药交易|仿真枪", "涉及枪支类业务"),
    (r"洗钱|跑分平台|刷单返利|代币发行|ICO|虚拟币交易所", "涉及金融黑灰产类业务"),
    (r"外挂|破解补丁|盗号|撞库", "涉及攻击或盗号类业务"),
)


def _iter_repo_text_files(repo: Path):
    for path in sorted(repo.rglob("*")):
        if not path.is_file():
            continue
        if any(part in rules.SCAN_IGNORED_DIR_PARTS for part in path.parts):
            continue
        yield path


def count_code_lines(repo: Path) -> dict[str, object]:
    total = 0
    per_suffix: dict[str, int] = {}
    skipped_locks: list[str] = []
    files = 0
    for path in _iter_repo_text_files(repo):
        if path.name in LOCK_FILE_NAMES:
            skipped_locks.append(str(path.relative_to(repo)))
            continue
        if path.suffix.lower() not in CODE_SUFFIXES:
            continue
        try:
            if path.stat().st_size > MAX_TEXT_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = len(text.rstrip("\n").splitlines())
        total += lines
        per_suffix[path.suffix.lower()] = per_suffix.get(path.suffix.lower(), 0) + lines
        files += 1
    return {
        "code_lines": total,
        "code_files": files,
        "per_suffix": dict(sorted(per_suffix.items(), key=lambda item: -item[1])),
        "skipped_lock_files": skipped_locks,
    }


def detect_repo_kind(repo: Path) -> str:
    """仓库形态：读前端 package.json 里的 repoKind，缺省当全栈。

    用显式字段而不是「没有 backend 目录就算纯前端」：后者会让「后端目录被误删的全栈仓库」
    悄悄降级通过，闸门就没意义了。
    """
    candidates = [repo / name for name in ("frontend", "web", "frontend-portal")]
    candidates += sorted(p for p in repo.iterdir()
                         if p.is_dir() and p.name.startswith(("frontend", "web")))
    for candidate in candidates:
        package_json = candidate / "package.json"
        if not package_json.is_file():
            continue
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("repoKind", "")).strip().lower() == "frontend-only":
            return "frontend-only"
    return "fullstack"


def check_structure(repo: Path, kind: str = "fullstack") -> dict[str, object]:
    problems: list[str] = []
    frontend_dir = next(
        (p for p in (repo / "frontend", repo / "web", repo / "frontend-portal") if p.is_dir()),
        None,
    )
    if frontend_dir is None:
        candidates = sorted(p for p in repo.iterdir() if p.is_dir() and p.name.startswith(("frontend", "web")))
        frontend_dir = candidates[0] if candidates else None
    if frontend_dir is None:
        problems.append("找不到前端目录（期望 frontend / web / frontend-*）")
    backend_dir = next(
        (p for p in (repo / "backend", repo / "server", repo / "api") if p.is_dir()),
        None,
    )
    # 纯前端仓库（frontend/package.json 里 repoKind = frontend-only）不要求后端目录；
    # 全栈仓库少一个后端目录就是结构不完整，照样拦。
    if backend_dir is None and kind != "frontend-only":
        problems.append("找不到后端目录（期望 backend / server / api）")

    frontend_stack = ""
    if frontend_dir:
        package_json = frontend_dir / "package.json"
        if not package_json.is_file():
            problems.append(f"{frontend_dir.name}/ 缺少 package.json")
        else:
            payload = json.loads(package_json.read_text(encoding="utf-8"))
            deps = {**payload.get("dependencies", {}), **payload.get("devDependencies", {})}
            for name, markers in FRONTEND_MARKERS.items():
                if any(marker in deps for marker in markers):
                    frontend_stack = name
                    break
            if not frontend_stack:
                problems.append("前端既不是 Vue 也不是 React（package.json 里没有对应依赖）")

    backend_stack = ""
    if backend_dir:
        blob = ""
        for candidate in ("requirements.txt", "pyproject.toml", "pom.xml", "build.gradle"):
            path = backend_dir / candidate
            if path.is_file():
                blob += path.read_text(encoding="utf-8", errors="replace").lower()
        has_python_src = any((backend_dir / "app").rglob("*.py")) if (backend_dir / "app").is_dir() else False
        has_java_src = any(backend_dir.rglob("*.java"))
        if has_java_src or "spring-boot" in blob or "pom.xml" in blob:
            backend_stack = "java"
        elif has_python_src or any(name in blob for name in ("fastapi", "flask", "django", "uvicorn")):
            backend_stack = "python"
        else:
            problems.append("后端既不是 Java 也不是 Python（没有 .java / .py 源码与对应依赖清单）")

    return {
        "ok": not problems,
        "problems": problems,
        "kind": kind,
        "frontend_dir": frontend_dir.name if frontend_dir else "",
        "frontend_stack": frontend_stack,
        "backend_dir": backend_dir.name if backend_dir else "",
        "backend_stack": backend_stack,
    }


def check_runnable_entries(repo: Path, structure: dict[str, object]) -> dict[str, object]:
    """启动入口是否齐全：前端有 dev 脚本，后端有可执行入口与健康检查。"""
    problems: list[str] = []
    evidence: dict[str, object] = {}
    frontend_dir = repo / str(structure["frontend_dir"]) if structure["frontend_dir"] else None
    backend_dir = repo / str(structure["backend_dir"]) if structure["backend_dir"] else None

    if frontend_dir:
        payload = json.loads((frontend_dir / "package.json").read_text(encoding="utf-8"))
        scripts = payload.get("scripts", {})
        evidence["frontend_scripts"] = sorted(scripts)
        if "dev" not in scripts:
            problems.append("前端 package.json 没有 dev 脚本，起不了页面")
        # 纯前端仓库的「启动入口」还包括本地数据层：没有它页面就没有数据来源。
        if str(structure.get("kind")) == "frontend-only":
            service = frontend_dir / "src" / "api" / "local-service.ts"
            data_dir = frontend_dir / "src" / "data"
            evidence["local_data_layer"] = {
                "service": str(service.relative_to(frontend_dir)) if service.is_file() else "",
                "data_files": sorted(p.name for p in data_dir.glob("*.ts")) if data_dir.is_dir() else [],
            }
            if not service.is_file():
                problems.append("纯前端仓库缺少本地数据层 frontend/src/api/local-service.ts")
            if not data_dir.is_dir() or not list(data_dir.glob("*.ts")):
                problems.append("纯前端仓库缺少 frontend/src/data/ 下的本地数据定义与示例数据")

    if backend_dir:
        if structure["backend_stack"] == "python":
            entry_ok = False
            for candidate in ("app/main.py", "main.py", "app.py", "run.sh"):
                if (backend_dir / candidate).is_file():
                    entry_ok = True
                    evidence.setdefault("backend_entries", []).append(candidate)
            if not entry_ok:
                problems.append("后端找不到入口（app/main.py / main.py / app.py / run.sh 都没有）")
            else:
                blob = "\n".join(
                    (backend_dir / candidate).read_text(encoding="utf-8", errors="replace")
                    for candidate in evidence.get("backend_entries", [])
                    if (backend_dir / candidate).is_file()
                )
                if "/api/health" not in blob and "/health" not in blob:
                    problems.append("后端没有健康检查端点（/api/health 或 /health）")
        else:
            if not (backend_dir / "pom.xml").is_file() and not (backend_dir / "build.gradle").is_file():
                problems.append("Java 后端缺 pom.xml / build.gradle")
            application = list(backend_dir.rglob("Application.java"))
            evidence["java_application"] = [str(p.relative_to(backend_dir)) for p in application]
            if not application:
                problems.append("Java 后端找不到 @SpringBootApplication 入口")
            controllers = list(backend_dir.rglob("*Controller.java"))
            if len(controllers) < 3:
                problems.append(f"Java 后端只有 {len(controllers)} 个 Controller，撑不起多主体出题")
    return {"ok": not problems, "problems": problems, "evidence": evidence}


def check_auto_open(repo: Path) -> dict[str, object]:
    """前端 dev server 必须关掉「自动打开页面」。"""
    problems: list[str] = []
    checked: list[str] = []
    explicit_off: list[str] = []
    for path in sorted(repo.rglob("*")):
        if not path.is_file():
            continue
        if any(part in rules.SCAN_IGNORED_DIR_PARTS for part in path.parts):
            continue
        name = path.name
        if not (name.startswith("vite.config") or name.startswith("webpack.config")
                or name.startswith("vue.config") or name in {"package.json", ".env", ".env.development",
                                                              ".env.local", "craco.config.js"}):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(repo))
        checked.append(rel)
        if re.search(r"open\s*:\s*false", text) or re.search(r"BROWSER\s*=\s*none", text):
            explicit_off.append(rel)
        for pattern, why in rules.AUTO_OPEN_PATTERNS:
            if name == "package.json" and pattern.startswith("open\\s"):
                continue
            if re.search(pattern, text):
                problems.append(f"{rel}：{why}")

    if not explicit_off:
        problems.append(
            "没有任何一处显式关掉自动打开页面：vite 要写 server.open = false，"
            "CRA 类项目要写 BROWSER=none")
    return {
        "ok": not problems,
        "problems": problems,
        "checked_files": checked,
        "explicit_off": explicit_off,
    }


def check_gitignore(repo: Path) -> dict[str, object]:
    """根目录 .gitignore 覆盖依赖 / 缓存 / 构建产物，且依赖目录确实没被跟踪。"""
    problems: list[str] = []
    missing: list[str] = []
    gitignore = repo / ".gitignore"
    if not gitignore.is_file():
        return {"ok": False, "problems": ["仓库根目录没有 .gitignore"], "missing_rules": list(
            rules.REQUIRED_GITIGNORE_RULES)}
    text = gitignore.read_text(encoding="utf-8", errors="replace")
    for label, candidates in rules.REQUIRED_GITIGNORE_RULES.items():
        if not any(candidate in text for candidate in candidates):
            missing.append(label)
    if missing:
        problems.append("`.gitignore` 没覆盖：" + "、".join(missing))

    ignored_dirs: list[str] = []
    untracked_ok = True
    if (repo / ".git").exists():
        import subprocess
        for directory in rules.iter_dependency_dirs(repo):
            rel = str(directory.relative_to(repo))
            result = subprocess.run(["git", "check-ignore", "-q", rel], cwd=repo,
                                    capture_output=True, text=True)
            if result.returncode != 0:
                problems.append(f"依赖/构建目录 {rel} 没有被 .gitignore 命中")
                untracked_ok = False
            else:
                ignored_dirs.append(rel)
        # 反向检查：业务页面目录**必须能进仓库**。模块 key 一旦撞上构建产物规则
        # （例如 views/target 撞 `**/target/`），页面就不会被提交——本地看着文件都在，
        # 干净克隆里少一个页面，前端构建直接报 TS2307。这条只能靠反向检查抓。
        for views in sorted(repo.glob("frontend*/src/views")) + sorted(repo.glob("web*/src/views")):
            for module_dir in sorted(path for path in views.iterdir() if path.is_dir()):
                rel = str(module_dir.relative_to(repo))
                result = subprocess.run(["git", "check-ignore", "-q", rel], cwd=repo,
                                        capture_output=True, text=True)
                if result.returncode == 0:
                    problems.append(
                        f"业务模块目录 {rel} 被 .gitignore 命中，页面不会进仓库："
                        "模块 key 和依赖 / 构建产物目录撞名了，换个业务化的 key")
                    untracked_ok = False
        status = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                                capture_output=True, text=True)
        tracked_junk = [
            line for line in status.stdout.splitlines()
            if any(part in line for part in ("node_modules/", ".venv/", "dist/", "__pycache__/"))
        ]
        if tracked_junk:
            problems.append("依赖/构建目录出现在 git 待提交列表里：" + "；".join(tracked_junk[:5]))
            untracked_ok = False
    else:
        untracked_ok = None
    return {
        "ok": not problems,
        "problems": problems,
        "missing_rules": missing,
        "ignored_dependency_dirs": ignored_dirs,
        "dependency_dirs_ignored": untracked_ok,
        "gitignore_bytes": len(text),
    }


def check_forbidden(repo: Path, structure: dict[str, object]) -> dict[str, object]:
    """合规扫描：禁用题型、密钥、二进制大块、违规业务域、严禁出题题域。"""
    problems: list[str] = []
    secret_hits: list[str] = []
    content_hits: list[str] = []
    big_files: list[str] = []
    topic_hits: set[str] = set()
    # 题域扫描的第一站是仓库目录名：下游 check_repo.py 判的就是「仓库名 + README」。
    for category, keyword in rules.scan_banned_topics(repo.name):
        topic_hits.add(f"仓库目录名：{rules.describe_banned_topic(category, keyword)}")
    for path in _iter_repo_text_files(repo):
        rel = str(path.relative_to(repo))
        size = path.stat().st_size
        if path.suffix.lower() in BINARY_SUFFIXES:
            if size > MAX_TEXT_FILE_BYTES:
                big_files.append(f"{rel} ({size // 1024}KB)")
            continue
        if size > MAX_TEXT_FILE_BYTES:
            big_files.append(f"{rel} ({size // 1024}KB)")
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # 题域扫描覆盖所有文本文件（README、配置、页面文案都算）。比下游只读 README
        # 更严一档：README 的模块表里就带着模块 key 与业务字段，源码命中基本等于 README 也会命中。
        for category, keyword in rules.scan_banned_topics(text):
            topic_hits.add(f"{rel}：{rules.describe_banned_topic(category, keyword)}")
        if path.suffix.lower() not in CODE_SUFFIXES:
            continue
        for pattern, why in SECRET_PATTERNS:
            if re.search(pattern, text):
                secret_hits.append(f"{rel}：{why}")
        for pattern, why in FORBIDDEN_CONTENT_PATTERNS:
            if re.search(pattern, text):
                content_hits.append(f"{rel}：{why}")
    if secret_hits:
        problems.append("疑似密钥/口令：" + "；".join(secret_hits[:5]))
    if content_hits:
        problems.append("违规业务内容：" + "；".join(content_hits[:5]))
    if big_files:
        problems.append("存在超大文件，产物快照会被判 too_large：" + "；".join(big_files[:5]))
    if topic_hits:
        problems.append(
            f"命中严禁出题题域，命中即废题（依据 {rules.BANNED_TOPIC_SOURCE}）："
            + "；".join(sorted(topic_hits)[:8])
            + "。换题域重做，不要靠改字眼绕过去")

    # 禁用题型：仓库必须能出「代码生成 / 功能迭代 / 缺陷修复 / 代码重构 / 工程化」，
    # 不能做成只能讲清楚代码怎么走的仓库（只有文档、没有可改可跑的业务代码）。
    # 纯前端仓库只看页面数；有后端的仓库接口文件也要够。
    if structure["frontend_dir"]:
        frontend_dir = repo / str(structure["frontend_dir"])
        views = [p for p in frontend_dir.rglob("index.*") if p.suffix in {".vue", ".tsx", ".jsx"}]
        if len(views) < 8:
            problems.append(
                f"可独立出题的页面太少（页面 {len(views)} 个）："
                "仓库会退化成只能出代码理解类题，必须补齐业务模块")
        backend_name = str(structure.get("backend_dir") or "")
        if backend_name:
            backend_dir = repo / backend_name
            routers = [p for p in (backend_dir / "app" / "routers").glob("*.py")] if (
                backend_dir / "app" / "routers").is_dir() else list(backend_dir.rglob("*Controller.java"))
            if len(routers) < 8:
                problems.append(
                    f"可独立出题的接口太少（接口文件 {len(routers)} 个）："
                    "仓库会退化成只能出代码理解类题，必须补齐业务模块")
    return {
        "ok": not problems,
        "problems": problems,
        "secret_hits": secret_hits,
        "forbidden_content_hits": content_hits,
        "big_files": big_files,
        "banned_topic_hits": sorted(topic_hits),
    }


JS_IMPORT_RE = re.compile(r"""(?:from|import)\s*\(?\s*['"]([^'"]+)['"]""")
PY_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import\s|import\s+([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*))",
    re.MULTILINE,
)
# 会写死在源码里、换台机器就崩的绝对路径。
LOCAL_PATH_RE = re.compile(
    r"(/Users/[A-Za-z0-9._-]+|/home/[A-Za-z0-9._-]+|/private/tmp/|/var/folders/|[A-Z]:\\\\Users\\\\)")


# 从别的语言抄过来、在 Python 里是合法语法但运行必炸的字面量。
PY_BARE_LITERALS = {"true", "false", "null", "undefined"}


def _bare_python_literals(text: str) -> list[int]:
    """找出把 true/false/null 当变量名用的行：语法合法，一 import 就 NameError。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id in PY_BARE_LITERALS]


def _python_entry_module(backend_dir: Path) -> str:
    for relative, module in (("app/main.py", "app.main"), ("main.py", "main"), ("app.py", "app")):
        if (backend_dir / relative).is_file():
            return module
    return ""


def _backend_import_smoke(backend_dir: Path, declared: set[str]) -> dict[str, object]:
    """真的 import 一次后端入口。

    编译只能抓语法错误，抓不到「少个依赖」「写了个不存在的名字」这类一 import 就炸的问题，
    所以这里直接跑一次导入。缺的包如果是 requirements 里声明过的（只是还没装），
    就交给真启动探针去覆盖，不算失败。
    """
    module = _python_entry_module(backend_dir)
    if not module:
        return {"skipped": "找不到后端入口模块"}
    venv_python = backend_dir / ".venv" / "bin" / "python"
    python = str(venv_python) if venv_python.exists() else sys.executable
    environment = {**os.environ, "PYTHONPATH": str(backend_dir)}
    result = subprocess.run([python, "-c", f"import {module}"], cwd=backend_dir,
                            capture_output=True, text=True, env=environment, timeout=180)
    if result.returncode == 0:
        return {"module": module, "python": python, "ok": True}
    stderr = result.stderr.strip()
    missing = re.search(r"ModuleNotFoundError: No module named '([^']+)'", stderr)
    if missing:
        top = missing.group(1).split(".")[0].lower().replace("-", "_")
        if top in declared:
            return {"module": module, "python": python, "ok": True,
                    "deferred": f"依赖 {top} 还没装，交给启动探针覆盖"}
    return {"module": module, "python": python, "ok": False, "error": stderr[-600:]}


def _declared_python_packages(backend_dir: Path) -> set[str]:
    declared: set[str] = set()
    for name in ("requirements.txt", "requirements-dev.txt"):
        path = backend_dir / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            package = re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].strip().lower().replace("-", "_")
            if package:
                declared.add(package)
    for name in ("pyproject.toml",):
        path = backend_dir / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in re.findall(r'"([A-Za-z0-9_.-]+)\s*(?:[<>=!~\["].*?)?"', text):
            declared.add(match.strip().lower().replace("-", "_"))
    return declared


def _declared_node_packages(frontend_dir: Path) -> set[str]:
    package_json = frontend_dir / "package.json"
    if not package_json.is_file():
        return set()
    payload = json.loads(package_json.read_text(encoding="utf-8"))
    return set(payload.get("dependencies", {})) | set(payload.get("devDependencies", {}))


def check_env_preconditions(repo: Path, structure: dict[str, object]) -> dict[str, object]:
    """基础环境自洽：代码能编译、依赖都声明了、没有写死本机路径。

    这一条专治「本地能跑、换个环境就炸」：语法错误、少写依赖、把 /Users/xxx 写进源码，
    都是基础环境问题，必须在交付前拦下，而不是等探针起来才发现。
    """
    problems: list[str] = []
    evidence: dict[str, object] = {}

    # 1) 所有 Python 文件必须能编译
    broken: list[str] = []
    bare_literals: list[str] = []
    py_files = 0
    for path in _iter_repo_text_files(repo):
        if path.suffix != ".py":
            continue
        py_files += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            compile(text, str(path), "exec")
        except SyntaxError as error:
            broken.append(f"{path.relative_to(repo)}:{error.lineno} {error.msg}")
            continue
        for line in _bare_python_literals(text):
            bare_literals.append(f"{path.relative_to(repo)}:{line}")
    evidence["python_files"] = py_files
    if broken:
        problems.append("Python 文件编译失败：" + "；".join(broken[:5]))
    if bare_literals:
        problems.append(
            "Python 里把 true/false/null 当变量名用了（一 import 就 NameError）："
            + "、".join(bare_literals[:5]))

    # 2) 写死本机绝对路径
    hardcoded: list[str] = []
    for path in _iter_repo_text_files(repo):
        if path.suffix.lower() not in CODE_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if LOCAL_PATH_RE.search(text):
            hardcoded.append(str(path.relative_to(repo)))
    if hardcoded:
        problems.append("源码里写死了本机绝对路径，换台机器就跑不起来：" + "、".join(hardcoded[:5]))

    # 3) Python 三方依赖必须写进 requirements
    if structure.get("backend_stack") == "python" and structure.get("backend_dir"):
        backend_dir = repo / str(structure["backend_dir"])
        declared = _declared_python_packages(backend_dir)
        local_packages = {path.name for path in backend_dir.iterdir()
                          if path.is_dir() and (path / "__init__.py").is_file()}
        stdlib = set(getattr(sys, "stdlib_module_names", set()))
        imported: set[str] = set()
        for path in backend_dir.rglob("*.py"):
            if any(part in rules.SCAN_IGNORED_DIR_PARTS for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in PY_IMPORT_RE.finditer(text):
                for group in match.groups():
                    if not group:
                        continue
                    for item in group.split(","):
                        imported.add(item.strip().split(".")[0])
        missing = sorted(name for name in imported
                         if name and name not in stdlib and name not in declared
                         and name not in local_packages and not name.startswith("_"))
        evidence["python_declared"] = sorted(declared)
        evidence["python_undeclared"] = missing
        if missing:
            problems.append(f"后端 import 了但 requirements 里没声明的包：{'、'.join(missing[:6])}")

        smoke = _backend_import_smoke(backend_dir, declared)
        evidence["backend_import_smoke"] = smoke
        if smoke.get("ok") is False:
            problems.append(f"后端入口 import 失败（{smoke.get('module')}）："
                            f"{str(smoke.get('error'))[-300:]}")

    # 4) 前端三方依赖必须写进 package.json
    if structure.get("frontend_dir"):
        frontend_dir = repo / str(structure["frontend_dir"])
        declared_node = _declared_node_packages(frontend_dir)
        stdlib_node = {"node:fs", "node:path", "node:url", "node:process", "node:crypto"}
        imported_node: set[str] = set()
        for path in frontend_dir.rglob("*"):
            if path.suffix not in {".ts", ".tsx", ".js", ".jsx", ".vue"} or not path.is_file():
                continue
            if any(part in rules.SCAN_IGNORED_DIR_PARTS for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for spec in JS_IMPORT_RE.findall(text):
                if spec.startswith((".", "/", "@/")) or spec in stdlib_node or spec.startswith("node:"):
                    continue
                parts = spec.split("/")
                package = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
                imported_node.add(package)
        missing_node = sorted(name for name in imported_node if name not in declared_node)
        evidence["node_declared"] = sorted(declared_node)
        evidence["node_undeclared"] = missing_node
        if missing_node:
            problems.append(f"前端 import 了但 package.json 里没声明的包：{'、'.join(missing_node[:6])}")

        # 5) 启动脚本齐全，且声明了构建命令
        payload = json.loads((frontend_dir / "package.json").read_text(encoding="utf-8"))
        scripts = payload.get("scripts", {})
        if "dev" not in scripts:
            problems.append("前端 package.json 没有 dev 脚本")
        if "build" not in scripts:
            problems.append("前端 package.json 没有 build 脚本，交付前无法做构建验收")

    return {"ok": not problems, "problems": problems, "evidence": evidence}


def check_capacity(repo: Path, *, min_capacity: int, per_mode: int, per_subject: int) -> dict[str, object]:
    derived = rules.derive_subjects(repo)
    capacity = rules.compute_capacity(
        derived, max_per_mode=per_mode, max_per_subject=per_subject)
    health = rules.derivation_health(repo, derived)
    problems: list[str] = []
    if health.get("warning"):
        problems.append(str(health["warning"]))
    if int(capacity["capacity"]) < min_capacity:
        problems.append(
            f"容量只有 {capacity['capacity']} 条（主体 {capacity['subject_count']} 个 x 每主体 "
            f"{per_subject} 条，需求模式 {capacity['usable_modes']} 类 x 每模式 {per_mode} 条，"
            f"单仓库上限 {capacity['max_per_repo']}），少于要求的 {min_capacity} 条")
    return {
        "ok": not problems,
        "problems": problems,
        "capacity": capacity["capacity"],
        "binding_limit": capacity["binding_limit"],
        "subject_count": capacity["subject_count"],
        "subject_slots": capacity["subject_slots"],
        "mode_slots": capacity["mode_slots"],
        "subjects": capacity["subjects"],
        "derivation_health": health,
        "rule": capacity["rule"],
    }


def banned_drift(path: Path) -> dict[str, object]:
    """只比对严禁题域清单，供 `--banned-drift` 使用。

    `--rules-drift` 比的是容量/主体那一套口径（check_repo_theme.py），
    而严禁清单的代码副本在 gsb-creator/scripts/check_repo.py 里，两者不是同一份脚本。
    单独开一条命令，语义才清楚：更新了 0731 清单，就跑这条确认快照没过期。
    """
    spec = importlib.util.spec_from_file_location("external_banned_source", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    theirs = None
    theirs_name = "absent"
    for candidate in ("BANNED_TOPIC_KEYWORDS", "BANNED_KEYWORDS"):
        value = getattr(module, candidate, None)
        if value is not None:
            theirs = value
            theirs_name = candidate
            break
    if theirs is None:
        return {
            "ok": False,
            "diffs": [f"{path} 里既没有 BANNED_TOPIC_KEYWORDS 也没有 BANNED_KEYWORDS"],
            "their_banned_keywords": "absent",
        }
    mine = {key: tuple(value) for key, value in rules.BANNED_TOPIC_KEYWORDS.items()}
    other = {key: tuple(value) for key, value in theirs.items()}
    diffs: list[str] = []
    for key in sorted(set(mine) | set(other)):
        if key not in other:
            diffs.append(f"分类 {key}：对方没有")
        elif key not in mine:
            diffs.append(f"分类 {key}：我方没有")
        elif set(mine[key]) != set(other[key]):
            only_mine = sorted(set(mine[key]) - set(other[key]))
            only_theirs = sorted(set(other[key]) - set(mine[key]))
            detail = []
            if only_mine:
                detail.append("我方多出 " + "、".join(only_mine))
            if only_theirs:
                detail.append("对方多出 " + "、".join(only_theirs))
            diffs.append(f"分类 {key} 词表不一致：" + "；".join(detail))
    return {
        "ok": not diffs,
        "diffs": diffs,
        "their_banned_keywords": theirs_name,
        "snapshot_date": rules.BANNED_TOPIC_SNAPSHOT_DATE,
    }


def rules_drift(path: Path) -> dict[str, object]:
    """比对冻结规则与另一台机器上实际生效的口径，防止快照悄悄过期。"""
    spec = importlib.util.spec_from_file_location("external_check_repo_theme", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    diffs: list[str] = []
    pairs = (
        ("REPO_SOURCE_PATTERNS", "REPO_SOURCE_PATTERNS"),
        ("PARENT_NAMED_STEMS", "PARENT_NAMED_STEMS"),
        ("GENERIC_DIR_NAMES", "GENERIC_DIR_NAMES"),
        ("GENERIC_TOKENS", "GENERIC_TOKENS"),
        ("IGNORED_SOURCE_DIR_PARTS", "IGNORED_SOURCE_DIR_PARTS"),
        ("DEMAND_MODE_LEXICON", "DEMAND_MODE_LEXICON"),
    )
    for mine_name, theirs_name in pairs:
        mine_value = getattr(rules, mine_name)
        theirs_value = getattr(module, theirs_name, None)
        if theirs_value is None:
            diffs.append(f"{theirs_name}：对方模块里没有这个常量")
            continue
        if isinstance(mine_value, dict):
            mine_norm = {key: tuple(value) for key, value in mine_value.items()}
            theirs_norm = {key: tuple(value) for key, value in theirs_value.items()}
        else:
            mine_norm = set(mine_value)
            theirs_norm = set(theirs_value)
        if mine_norm != theirs_norm:
            diffs.append(f"{mine_name} 与对方不一致")
    theirs_max_mode = getattr(module, "DEFAULT_MAX_PER_MODE", None)
    theirs_max_subject = getattr(module, "DEFAULT_MAX_PER_SUBJECT", None)
    theirs_max_repo = getattr(module, "DEFAULT_MAX_PER_REPO", None)

    # 严禁题域清单是「可选比对」。出题闸门脚本里这份表可能叫 BANNED_TOPIC_KEYWORDS，
    # 也可能叫 BANNED_KEYWORDS（gsb-creator/scripts/check_repo.py 就是这么命名的）。
    # 两个名字都没有时**静默跳过**：老的 check_repo_theme.py 不带这份清单，
    # 不该因为缺一个常量就把整次比对判成失败。
    theirs_banned = None
    theirs_banned_name = "absent"
    for candidate in ("BANNED_TOPIC_KEYWORDS", "BANNED_KEYWORDS"):
        value = getattr(module, candidate, None)
        if value is not None:
            theirs_banned = value
            theirs_banned_name = candidate
            break
    if theirs_banned is not None:
        mine_banned = {key: tuple(value) for key, value in rules.BANNED_TOPIC_KEYWORDS.items()}
        their_banned = {key: tuple(value) for key, value in theirs_banned.items()}
        if mine_banned != their_banned:
            diffs.append(f"BANNED_TOPIC_KEYWORDS 与对方的 {theirs_banned_name} 不一致")
    return {
        "ok": not diffs,
        "diffs": diffs,
        "their_defaults": {
            "max_per_mode": theirs_max_mode,
            "max_per_subject": theirs_max_subject,
            "max_per_repo": theirs_max_repo,
        },
        "their_banned_keywords": theirs_banned_name,
    }


def verify(repo: Path, *, min_lines: int, min_capacity: int, per_mode: int,
           per_subject: int, kind: str = "auto") -> dict[str, object]:
    resolved_kind = detect_repo_kind(repo) if kind == "auto" else kind
    checks: dict[str, object] = {}
    checks["structure"] = check_structure(repo, resolved_kind)
    checks["runnable_entries"] = check_runnable_entries(repo, checks["structure"])
    checks["auto_open"] = check_auto_open(repo)
    checks["gitignore"] = check_gitignore(repo)
    checks["code_size"] = count_code_lines(repo)
    checks["capacity"] = check_capacity(repo, min_capacity=min_capacity,
                                        per_mode=per_mode, per_subject=per_subject)
    checks["env_preconditions"] = check_env_preconditions(repo, checks["structure"])
    checks["compliance"] = check_forbidden(repo, checks["structure"])

    failures: list[str] = []
    if int(checks["code_size"]["code_lines"]) < min_lines:
        failures.append(
            f"G4 代码量不足：{checks['code_size']['code_lines']} 行 < {min_lines} 行")
    label_map = {
        "structure": "G1 结构",
        "runnable_entries": "G1 启动入口",
        "auto_open": "G2 自动打开页面",
        "gitignore": "G3 .gitignore",
        "capacity": "G5 出题容量",
        "compliance": "G6 内容合规",
        "env_preconditions": "G7 基础环境自洽",
    }
    for key, label in label_map.items():
        if not checks[key]["ok"]:
            failures.append(f"{label} 未通过：" + "；".join(checks[key]["problems"]))
    return {
        "ok": not failures,
        "repo": str(repo),
        "kind": resolved_kind,
        "gate_version": rules.GATE_VERSION,
        "failures": failures,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="验收代码仓库是否满足本 skill 的硬性要求")
    parser.add_argument("--repo", help="要验收的仓库目录")
    parser.add_argument("--min-lines", type=int, default=2000, help="源码行数下限，默认 2000")
    parser.add_argument("--min-capacity", type=int, default=rules.MIN_CAPACITY,
                        help=f"出题容量下限，默认 {rules.MIN_CAPACITY}")
    parser.add_argument("--per-mode", type=int, default=rules.MAX_PER_MODE,
                        help=f"每类需求模式最多出几条，默认 {rules.MAX_PER_MODE}")
    parser.add_argument("--per-subject", type=int, default=rules.MAX_PER_SUBJECT,
                        help=f"每个主体最多出几条，默认 {rules.MAX_PER_SUBJECT}")
    parser.add_argument("--kind", choices=["auto", "fullstack", "frontend-only"], default="auto",
                        help="仓库形态；默认 auto = 读前端 package.json 的 repoKind 字段判断")
    parser.add_argument("--rules-drift",
                        help="比对另一台机器上的 check_repo_theme.py 口径（容量/主体/需求模式）")
    parser.add_argument("--banned-drift",
                        help="只比对严禁题域清单，指向 gsb-creator/scripts/check_repo.py")
    parser.add_argument("--json", action="store_true", help="只打印 JSON")
    args = parser.parse_args()

    if args.banned_drift:
        result = banned_drift(Path(args.banned_drift).expanduser())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    if args.rules_drift:
        result = rules_drift(Path(args.rules_drift).expanduser())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    if not args.repo:
        parser.error("要么给 --repo，要么给 --rules-drift / --banned-drift")
    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir():
        print(json.dumps({"ok": False, "failures": [f"仓库目录不存在：{repo}"]}, ensure_ascii=False))
        return 2

    result = verify(repo, min_lines=args.min_lines, min_capacity=args.min_capacity,
                    per_mode=args.per_mode, per_subject=args.per_subject, kind=args.kind)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "通过" if result["ok"] else "未通过"
        print(f"[{status}] {result['repo']}（形态：{result['kind']}）")
        size = result["checks"]["code_size"]
        cap = result["checks"]["capacity"]
        print(f"  代码量：{size['code_lines']} 行 / {size['code_files']} 个文件")
        print(f"  出题容量：{cap['capacity']} 条（主体 {cap['subject_count']} 个，"
              f"卡在 {cap['binding_limit']}）")
        for failure in result["failures"]:
            print(f"  ✗ {failure}")
        topic_hits = result["checks"]["compliance"].get("banned_topic_hits") or []
        if topic_hits:
            print(f"  ✗ 严禁题域命中 {len(topic_hits)} 处：{'；'.join(topic_hits[:8])}")
        if not result["failures"]:
            print("  全部硬性要求通过")
            print(f"  严禁题域：未命中（{rules.BANNED_TOPIC_SOURCE}）")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
