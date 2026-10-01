#!/usr/bin/env python3
"""生成一个满足硬性要求的代码仓库（全栈，或纯前端）。

硬性要求（全部由 scripts/verify_repo.py 机器复核，别手工判断）：

  1. 前端 Vue 或 React；后端 Java 或 Python（`--backend none` 时只出纯前端，
     数据走 `frontend/src/data` 下的本地数据层，不再要求后端目录）。
  2. 前端 dev server 关掉「自动打开页面」（vite 的 server.open = false）。
  3. 仓库根目录有 .gitignore，覆盖依赖、缓存、构建产物、日志、环境文件等。
  4. 源码总行数 >= 2000 行（不含依赖目录、构建产物、锁文件）。
  5. 不含禁用题型（代码理解），业务代码落在扫描模式覆盖的路径里。
  6. 主体数与需求模式数算出来的容量 >= 30（见 references/hard-rules.md）。
  7. **题域不能命中 0731 严禁出题清单**（命中即废题）。这条在生成之前就拦：
     spec 的应用名 / 描述 / 模块名 / 业务字段 / 状态 / 动作里出现禁用词就直接拒绝生成，
     没有绕过参数。命中即换题域，别改措辞绕。

用法：

    python3 scaffold_repo.py --out /path/to/repo --spec spec.json
    python3 scaffold_repo.py --out /path/to/repo --frontend react --backend java
    python3 scaffold_repo.py --out /path/to/repo --backend none         # 纯前端仓库
    python3 scaffold_repo.py --out /path/to/repo --git-init      # 顺手 git init + 首次提交
    python3 scaffold_repo.py --check-spec --spec spec.json       # 只自检题域，不生成任何目录

生成完不会自动装依赖；装依赖、起服务、验收统一走 SKILL.md 的「验收」一节。
"""

from __future__ import annotations

import argparse
import json
import pprint
import re
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import repo_rules as rules  # noqa: E402

DEFAULT_SPEC_NAME = "spec-film-production.json"

# 项目编号：<前缀 2~4 个字母><5 位数>，5 位数首位固定是 1（10001–19999），例如 cc10001。
PROJECT_ID_RE = re.compile(r"^([A-Za-z]{2,4})(1\d{4})$")
DEFAULT_PREFIX = "cc"
NUMBER_FLOOR = 10001
NUMBER_CEILING = 19999
# 最多往后找多少个编号：本地 + GitHub 都占用时才会继续往后，正常不会用到这么多。
MAX_NUMBER_PROBES = 200

# 模块 key 会变成 frontend/src/views/<key>/ 的目录名。下面这些名字在生成的 .gitignore 里
# 是依赖 / 构建产物规则（`dist/`、`target/`、`**/target/`…），撞名会让整个模块的页面被 git
# 忽略：本地看着文件都在，干净克隆里少一个页面，前端构建直接报 TS2307。真踩过一次
# （模块 key = target，被 `**/target/` 吃掉），所以在 spec 阶段就禁止，不留到探针才发现。
RESERVED_MODULE_KEYS = frozenset({
    "node_modules", "dist", "build", "out", "target", "coverage", "logs",
    "classes", "eggs", "env", "venv", "__pycache__",
})


def validate_prefix(prefix: str) -> str:
    if not re.fullmatch(r"[A-Za-z]{2,4}", prefix or ""):
        raise SystemExit(f"前缀必须是 2~4 个字母（例如 cc / YEL），收到 {prefix!r}")
    return prefix


def split_project_id(name: str) -> tuple[str, int] | None:
    match = PROJECT_ID_RE.match(name)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def local_numbers(directory: Path) -> set[int]:
    """扫这个目录里已经存在的编号（不区分前缀，编号在同一序列里往后走）。"""
    numbers: set[int] = set()
    if not directory.is_dir():
        return numbers
    for child in sorted(directory.iterdir()):
        parsed = split_project_id(child.name)
        if parsed:
            numbers.add(parsed[1])
    return numbers


def gh_binary() -> str | None:
    return shutil.which("gh")


def resolve_owner(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    gh = gh_binary()
    if not gh:
        return None
    result = subprocess.run([gh, "api", "user", "-q", ".login"],
                            capture_output=True, text=True)
    owner = result.stdout.strip()
    return owner if result.returncode == 0 and owner else None


def github_repo_exists(owner: str, name: str) -> bool | None:
    """True/False 表示 GitHub 上的查询结果；None 表示查不了（没装 gh 或没登录）。"""
    gh = gh_binary()
    if not gh or not owner:
        return None
    result = subprocess.run([gh, "repo", "view", f"{owner}/{name}"],
                            capture_output=True, text=True)
    return result.returncode == 0


def pick_project_id(
    parent: Path,
    prefix: str,
    *,
    explicit_number: int | None,
    owner: str | None,
    check_github: bool,
) -> dict[str, object]:
    """定下这个项目的编号：本地已有的往后排，GitHub 上已存在的也往后排。"""
    prefix = validate_prefix(prefix)
    if explicit_number is not None:
        if not (NUMBER_FLOOR <= int(explicit_number) <= NUMBER_CEILING):
            raise SystemExit(
                f"--number 必须是 {NUMBER_FLOOR}~{NUMBER_CEILING} 之间的 5 位数，首位是 1")
        name = f"{prefix}{int(explicit_number)}"
        if (parent / name).exists():
            raise SystemExit(f"编号 {name} 的目录已经存在：{parent / name}")
        if check_github and github_repo_exists(owner, name):
            raise SystemExit(f"GitHub 上已经有 {owner}/{name}，换一个 --number 或去掉它让脚本自动往后排")
        return {
            "project_id": name, "prefix": prefix, "number": int(explicit_number),
            "picked_from": "explicit", "github_checked": bool(check_github and owner),
            "skipped": [],
        }

    known = local_numbers(parent)
    start = max(known) + 1 if known else NUMBER_FLOOR
    skipped: list[dict[str, str]] = []
    github_checked = {"value": False}
    for number in range(start, start + MAX_NUMBER_PROBES):
        if number > NUMBER_CEILING:
            raise SystemExit(
                f"编号已经超过 {NUMBER_CEILING}（5 位数首位是 1 的上限），"
                "换一个前缀或手工清理一批编号再建")
        name = f"{prefix}{number}"
        if (parent / name).exists():
            skipped.append({"project_id": name, "reason": "本地目录已存在"})
            continue
        exists = github_repo_exists(owner, name) if check_github else None
        if exists is not None:
            github_checked["value"] = True
        if exists:
            skipped.append({"project_id": name, "reason": "GitHub 上已存在"})
            continue
        return {
            "project_id": name, "prefix": prefix, "number": number,
            "picked_from": f"本地最大编号 {max(known)} 往后排" if known else "从 10001 起",
            "github_checked": github_checked["value"], "skipped": skipped,
        }
    raise SystemExit(f"往后找了 {MAX_NUMBER_PROBES} 个编号都被占用，先人工清理再建")


# ---------------------------------------------------------------------------
# spec 读取
# ---------------------------------------------------------------------------
SPEC_TOPIC_SCALARS = ("app_name", "app_slug", "app_desc")
SPEC_TOPIC_LIST_FIELDS = ("fields", "statuses", "actions", "action_targets", "metrics")
SPEC_TOPIC_MODULE_SCALARS = ("key", "name", "entity", "desc")


def spec_topic_corpus(spec: dict) -> dict[str, str]:
    """把 spec 摊成「字段路径 -> 文本」，供严禁题域扫描逐字段定位命中点。

    覆盖应用级三个字段 + 每个模块的标识、名称、业务对象、说明，以及
    fields / statuses / actions / action_targets / metrics 五组列表。
    """
    corpus: dict[str, str] = {}
    for field in SPEC_TOPIC_SCALARS:
        corpus[field] = str(spec.get(field) or "")
    for index, module in enumerate(spec.get("modules") or [], start=1):
        key = str(module.get("key") or f"module{index}")
        for field in SPEC_TOPIC_MODULE_SCALARS:
            corpus[f"modules[{key}].{field}"] = str(module.get(field) or "")
        for field in SPEC_TOPIC_LIST_FIELDS:
            for item in module.get(field) or []:
                corpus[f"modules[{key}].{field}[]"] = (
                    corpus.get(f"modules[{key}].{field}[]", "") + "\n" + str(item))
    return corpus


def scan_spec_topics(spec: dict) -> list[dict[str, str]]:
    """扫描 spec 是否命中严禁出题题域，返回 [{category, keyword, label, field}]。"""
    hits: list[dict[str, str]] = []
    for field, text in spec_topic_corpus(spec).items():
        for category, keyword in rules.scan_banned_topics(text):
            hits.append({
                "category": category,
                "keyword": keyword,
                "label": rules.describe_banned_topic(category, keyword),
                "field": field,
            })
    return hits


def banned_topic_report(hits: list[dict[str, str]], spec_path: object = None) -> str:
    """把命中项渲染成给人看的报错文案。"""
    by_label: dict[str, set[str]] = {}
    for hit in hits:
        by_label.setdefault(hit["label"], set()).add(hit["field"])
    lines = [
        "题域命中 0731 严禁出题清单（命中即废题），拒绝生成。",
        f"依据：{rules.BANNED_TOPIC_SOURCE}（抄写日期 {rules.BANNED_TOPIC_SNAPSHOT_DATE}）",
    ]
    if spec_path is not None:
        lines.append(f"spec：{spec_path}")
    lines.append("命中项：")
    for label in sorted(by_label):
        fields = "、".join(sorted(by_label[label])[:6])
        lines.append(f"  - {label}  出现在 {fields}")
    lines += [
        "",
        "这不是措辞问题，是题域问题：换个方向重写 spec，不要靠改字眼绕过去。",
        "可用方向与禁用清单见 references/spec-format.md「严禁题域：这些方向不能选」一节。",
    ]
    return "\n".join(lines)


def load_spec(path: Path | None, frontend: str | None, backend: str | None) -> dict:
    if path is None:
        path = SKILL_DIR / "examples" / DEFAULT_SPEC_NAME
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    if frontend:
        spec["frontend"] = frontend
    if backend:
        spec["backend"] = backend
    spec.setdefault("frontend", "vue")
    spec.setdefault("backend", "python")
    if spec["frontend"] not in {"vue", "react"}:
        raise SystemExit(f"前端只支持 vue / react，收到 {spec['frontend']!r}")
    if spec["backend"] not in {"python", "java", "none"}:
        raise SystemExit(
            f"后端只支持 python / java / none（none = 纯前端仓库），收到 {spec['backend']!r}")
    modules = spec.get("modules") or []
    if not modules:
        raise SystemExit("spec 里没有任何 modules")
    seen: set[str] = set()
    for module in modules:
        for field in ("key", "name", "entity", "fields"):
            if not module.get(field):
                raise SystemExit(f"模块缺少字段 {field}：{module}")
        if module["key"] in seen:
            raise SystemExit(f"模块 key 重复：{module['key']}")
        if module["key"] in RESERVED_MODULE_KEYS:
            raise SystemExit(
                f"模块 key 不能叫 {module['key']!r}：它会变成 frontend/src/views/"
                f"{module['key']}/，而这个名字在生成的 .gitignore 里是依赖 / 构建产物规则，"
                "整个模块的页面会被 git 忽略、干净克隆里少文件、前端构建直接报错。"
                "换一个业务化的 key（例如 target 改成 threat）。")
        seen.add(module["key"])
        if len(module["fields"]) < 3:
            raise SystemExit(f"模块 {module['key']} 的 fields 少于 3 个，主体识别会不达标")
    topic_hits = scan_spec_topics(spec)
    if topic_hits:
        raise SystemExit(banned_topic_report(topic_hits, spec_path=path))
    return spec


def render(template: str, **values: object) -> str:
    text = template
    for key, value in values.items():
        text = text.replace(f"__{key}__", str(value))
    return text


def class_name(key: str) -> str:
    return "".join(part.capitalize() for part in key.replace("-", "_").split("_") if part)


def json_array(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(item, ensure_ascii=False) for item in items) + "]"


def ts_array(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(item, ensure_ascii=False) for item in items) + "]"


def py_list(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(item, ensure_ascii=False) for item in items) + "]"


def py_literal(value: object, *, indent: int | None = None) -> str:
    """把 Python 对象渲染成 Python 字面量。

    json.dumps 会把布尔值写成 true/false，直接落进 .py 文件就是 NameError；
    pprint 输出的是合法 Python 字面量，且中文不转义，所以用它。
    """
    width = 100 if indent is not None else 10_000
    return pprint.pformat(value, width=width, sort_dicts=False)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")


def line_count(text: str) -> int:
    return len(text.rstrip("\n").splitlines())


# ---------------------------------------------------------------------------
# gitignore / README / compose —— 三个技术栈共用
# ---------------------------------------------------------------------------
GITIGNORE = """\
# ===== 依赖目录 =====
node_modules/
**/node_modules/
.pnp
.pnp.js
.yarn/cache
.pnpm-store/
.venv/
venv/
env/
__pycache__/
*.py[cod]
*.egg-info/
.eggs/
dist/
eggs/
pip-wheel-metadata/

# ===== 构建产物 =====
dist/
build/
out/
target/
**/target/
.gradle/
classes/
*.class
*.jar
*.war
*.local

# ===== 包管理器 / 构建缓存 =====
.npm/
.cache/
.parcel-cache/
.vite/
.turbo/
.sass-cache/
*.tsbuildinfo
.eslintcache
.stylelintcache
.mypy_cache/
.ruff_cache/
.pytest_cache/
.tox/
.nox/

# ===== 日志 =====
logs/
*.log
npm-debug.log*
yarn-debug.log*
yarn-error.log*
pnpm-debug.log*

# ===== 环境变量与本地配置 =====
.env
.env.local
.env.*.local
*.env
!.env.example

# ===== 测试覆盖率 =====
coverage/
.nyc_output/
htmlcov/
*.lcov

# ===== IDE / 编辑器 =====
.idea/
.vscode/
*.iml
*.swp
*.swo
*~

# ===== 操作系统 =====
.DS_Store
.AppleDouble
Thumbs.db
desktop.ini

# ===== 运行期本地文件 =====
*.sqlite3
*.db
*.pid
*.seed
"""

README = """\
# __APP_NAME__

__APP_DESC__

这是一个前后端分离的管理平台：前端 __FRONTEND_LABEL__，后端 __BACKEND_LABEL__。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 __FRONTEND_LABEL__ 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  __BACKEND_LABEL__ 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
__BACKEND_SETUP__
__BACKEND_RUN__
```

健康检查：`curl http://127.0.0.1:__BACKEND_PORT__/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:__FRONTEND_PORT__/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:__BACKEND_PORT__`。

## 业务模块

__MODULE_TABLE__

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
"""

COMPOSE = """\
services:
  backend:
    build: ./backend
    ports:
      - "__BACKEND_PORT__:__BACKEND_PORT__"
    environment:
      - APP_ENV=local
  frontend:
    build: ./frontend
    ports:
      - "__FRONTEND_PORT__:__FRONTEND_PORT__"
    depends_on:
      - backend
"""

MAKEFILE = """\
.PHONY: install backend frontend

install:
\tcd backend && __BACKEND_INSTALL__
\tcd frontend && npm install

backend:
\tcd backend && __BACKEND_RUN__

frontend:
\tcd frontend && npm run dev
"""


# ---------------------------------------------------------------------------
# 前端：Vue
# ---------------------------------------------------------------------------
VITE_CONFIG_VUE = """\
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 后端地址默认取本文件里写的端口，起服务时可以用 VITE_PROXY_TARGET 覆盖，
// 这样换端口调试或做启动探针时前端不用改代码。
const proxyTarget = process.env.VITE_PROXY_TARGET ?? 'http://127.0.0.1:__BACKEND_PORT__'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: __FRONTEND_PORT__,
    // 关掉自动打开页面：起服务时只打印地址，不拉起浏览器
    open: false,
    strictPort: false,
    proxy: {
      '/api': {
        target: proxyTarget,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
"""

VITE_CONFIG_REACT = """\
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 后端地址默认取本文件里写的端口，起服务时可以用 VITE_PROXY_TARGET 覆盖，
// 这样换端口调试或做启动探针时前端不用改代码。
const proxyTarget = process.env.VITE_PROXY_TARGET ?? 'http://127.0.0.1:__BACKEND_PORT__'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: __FRONTEND_PORT__,
    // 关掉自动打开页面：起服务时只打印地址，不拉起浏览器
    open: false,
    strictPort: false,
    proxy: {
      '/api': {
        target: proxyTarget,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
"""

PACKAGE_JSON_VUE = """\
{
  "name": "__APP_SLUG__-frontend",
  "private": true,
  "version": "1.0.0",
  "type": "module",
  "repoKind": "__REPO_KIND__",
  "scripts": {
    "dev": "vite",
    "build": "vue-tsc --noEmit && vite build",
    "preview": "vite preview",
    "typecheck": "vue-tsc --noEmit"
  },
  "dependencies": {
    "pinia": "^2.1.7",
    "vue": "^3.4.21",
    "vue-router": "^4.3.0"
  },
  "devDependencies": {
    "@types/node": "^20.11.30",
    "@vitejs/plugin-vue": "^5.0.4",
    "typescript": "^5.4.3",
    "vite": "^5.2.6",
    "vue-tsc": "^2.0.7"
  }
}
"""

PACKAGE_JSON_REACT = """\
{
  "name": "__APP_SLUG__-frontend",
  "private": true,
  "version": "1.0.0",
  "type": "module",
  "repoKind": "__REPO_KIND__",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview",
    "typecheck": "tsc --noEmit"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.23.1"
  },
  "devDependencies": {
    "@types/node": "^20.11.30",
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "typescript": "^5.4.3",
    "vite": "^5.2.6"
  }
}
"""

INDEX_HTML = """\
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>__APP_NAME__</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.__MAIN_EXT__"></script>
  </body>
</html>
"""

TSCONFIG_VUE = """\
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "jsx": "preserve",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "esModuleInterop": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "skipLibCheck": true,
    "noEmit": true,
    "baseUrl": ".",
    "paths": { "@/*": ["src/*"] }
  },
  "include": ["src/**/*.ts", "src/**/*.d.ts", "src/**/*.vue", "vite.config.ts"]
}
"""

TSCONFIG_REACT = """\
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "jsx": "react-jsx",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "esModuleInterop": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "skipLibCheck": true,
    "noEmit": true,
    "baseUrl": ".",
    "paths": { "@/*": ["src/*"] }
  },
  "include": ["src/**/*.ts", "src/**/*.tsx", "vite.config.ts"]
}
"""

MAIN_VUE = """\
import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import './styles/global.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
"""

ENV_D_TS = """\
/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string
  readonly VITE_APP_NAME?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
"""

MAIN_REACT = """\
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import App from './App'
import './styles/global.css'

ReactDOM.createRoot(document.getElementById('app') as HTMLElement).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)
"""

APP_VUE = """\
<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">__APP_NAME__</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">__APP_DESC__</span>
        <span class="head-user">当前值班：{{ store.operator }} · {{ store.shiftLabel }}</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
import { useSessionStore } from '@/stores/session'

const store = useSessionStore()

const navItems = __NAV_ITEMS__
</script>
"""

APP_REACT = """\
import { useState } from 'react'
import { Link, Route, Routes } from 'react-router-dom'

import Dashboard from './views/Dashboard'
__REACT_VIEW_IMPORTS__

const navItems = __NAV_ITEMS__

export default function App() {
  const [operator] = useState('值班管理员')

  return (
    <div className="app-shell">
      <aside className="app-side">
        <h1 className="app-title">__APP_NAME__</h1>
        <nav className="nav-list">
          {navItems.map((item) => (
            <Link key={item.path} to={item.path} className="nav-item">
              {item.label}
            </Link>
          ))}
        </nav>
      </aside>
      <main className="app-main">
        <header className="app-head">
          <span className="head-desc">__APP_DESC__</span>
          <span className="head-user">当前值班：{operator}</span>
        </header>
        <Routes>
          <Route path="/" element={<Dashboard />} />
__REACT_ROUTES__
        </Routes>
      </main>
    </div>
  )
}
"""

ROUTER_TS = """\
import { createRouter, createWebHistory } from 'vue-router'

__VIEW_IMPORTS__

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: Dashboard },
__ROUTES__
  ],
})

export default router
"""

CLIENT_TS = """\
/** 统一请求封装：拼后端地址、抛网络错误、给页脚留一句可读的说明。 */
const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export function request(path: string, init?: RequestInit): Promise<Response> {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`
  return fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  }).catch((error: unknown) => {
    const detail = error instanceof Error ? error.message : '请求未送达'
    throw new Error(`接口请求失败：${detail}`)
  })
}

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) {
    throw new Error(`接口返回 ${response.status}，数据未更新`)
  }
  return (await response.json()) as T
}
"""

SESSION_STORE = """\
import { defineStore } from 'pinia'

export const useSessionStore = defineStore('session', {
  state: () => ({
    operator: '值班管理员',
    shiftLabel: '白班 08:00-20:00',
    scope: '__APP_SCOPE__',
  }),
  getters: {
    canOperate: (state) => state.operator.length > 0,
  },
  actions: {
    setShift(label: string) {
      this.shiftLabel = label
    },
  },
})
"""

GLOBAL_CSS = """\
:root {
  color-scheme: light;
  font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
  --brand: #1f6feb;
  --border: #d8dee6;
  --muted: #64748b;
}
* { box-sizing: border-box; }
body { margin: 0; background: #f6f8fb; color: #1f2937; }
.app-shell { display: flex; min-height: 100vh; }
.app-side { width: 220px; background: #101828; color: #e5e7eb; padding: 16px 12px; }
.app-title { font-size: 16px; margin: 0 0 16px; }
.nav-list { display: flex; flex-direction: column; gap: 4px; }
.nav-item { color: #cbd5e1; text-decoration: none; padding: 6px 8px; border-radius: 6px; font-size: 13px; }
.nav-item.router-link-active { background: #1f6feb; color: #fff; }
.app-main { flex: 1; padding: 16px 20px; }
.app-head { display: flex; justify-content: space-between; color: var(--muted); font-size: 13px; }
.page-head { display: flex; justify-content: space-between; align-items: flex-start; margin: 12px 0; }
.page-desc { color: var(--muted); font-size: 13px; margin: 4px 0 0; }
.stat-row { display: flex; gap: 12px; margin-bottom: 12px; }
.stat-card { flex: 1; background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }
.stat-label { display: block; color: var(--muted); font-size: 12px; }
.stat-value { font-size: 20px; }
.filter-bar { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; margin-bottom: 12px; }
.filter-item span { display: block; font-size: 12px; color: var(--muted); }
.data-table { width: 100%; border-collapse: collapse; background: #fff; }
.data-table th, .data-table td { border: 1px solid var(--border); padding: 8px 10px; font-size: 13px; text-align: left; }
.empty-state { text-align: center; color: var(--muted); }
.row-actions { display: flex; gap: 6px; }
.btn { border: 1px solid var(--border); background: #fff; border-radius: 6px; padding: 6px 12px; cursor: pointer; }
.btn.primary { background: var(--brand); border-color: var(--brand); color: #fff; }
.btn.ghost { background: transparent; }
.link { border: none; background: none; color: var(--brand); cursor: pointer; padding: 0; }
.page-foot { display: flex; justify-content: space-between; margin-top: 8px; font-size: 12px; color: var(--muted); }
.error-text { color: #b42318; }
"""

VUE_VIEW = """\
<template>
  <section class="page" data-module="__KEY__">
    <header class="page-head">
      <div>
        <h2>__NAME__管理</h2>
        <p class="page-desc">__DESC__</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记__ENTITY__</button>
        <button class="btn" type="button" @click="exportRows">导出__NAME__清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无__NAME__数据，可先登记__ENTITY__</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条__NAME__记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/__KEY__'
const columns = __FIELDS__
const actions = __ACTIONS__
const statuses = __STATUSES__
const stats = __METRICS__

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '__ENTITY__登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('__NAME__动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '__NAME__操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('__ENTITY__列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '__NAME__列表读取失败'
  }
}

onMounted(reload)
</script>
"""

REACT_VIEW = """\
import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/__KEY__'
const columns = __FIELDS__
const actions = __ACTIONS__
const statuses = __STATUSES__
const stats = __METRICS__

type Row = Record<string, string | number | null>

export default function __CLASS__View() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('__ENTITY__列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '__NAME__列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('__NAME__动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '__NAME__操作失败')
    }
  }

  return (
    <section className="page" data-module="__KEY__">
      <header className="page-head">
        <div>
          <h2>__NAME__管理</h2>
          <p className="page-desc">__DESC__</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('__ENTITY__登记入口尚未接入审批流')}>
            登记__ENTITY__
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出__NAME__清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无__NAME__数据，可先登记__ENTITY__</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条__NAME__记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
"""

DASHBOARD_VUE = """\
<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>运营概览</h2>
        <p class="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
      </div>
    </header>
    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>
    <table class="data-table">
      <thead>
        <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in moduleRows" :key="row.name">
          <td>{{ row.name }}</td>
          <td>{{ row.created }}</td>
          <td>{{ row.pending }}</td>
          <td>{{ row.abnormal }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchJson } from '@/api/client'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

const cards = ref<Overview['cards']>([])
const moduleRows = ref<Overview['modules']>([])

onMounted(async () => {
  try {
    const payload = await fetchJson<Overview>('/api/overview')
    cards.value = payload.cards
    moduleRows.value = payload.modules
  } catch {
    cards.value = __FALLBACK_CARDS__
    moduleRows.value = __FALLBACK_MODULES__
  }
})
</script>
"""

DASHBOARD_REACT = """\
import { useEffect, useState } from 'react'

import { fetchJson } from '@/api/client'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

const fallbackCards = __FALLBACK_CARDS__
const fallbackModules = __FALLBACK_MODULES__

export default function Dashboard() {
  const [cards, setCards] = useState<Overview['cards']>(fallbackCards)
  const [moduleRows, setModuleRows] = useState<Overview['modules']>(fallbackModules)

  useEffect(() => {
    fetchJson<Overview>('/api/overview')
      .then((payload) => {
        setCards(payload.cards)
        setModuleRows(payload.modules)
      })
      .catch(() => undefined)
  }, [])

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h2>运营概览</h2>
          <p className="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
        </div>
      </header>
      <div className="stat-row">
        {cards.map((card) => (
          <article key={card.label} className="stat-card">
            <span className="stat-label">{card.label}</span>
            <strong className="stat-value">{card.value}</strong>
          </article>
        ))}
      </div>
      <table className="data-table">
        <thead>
          <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
        </thead>
        <tbody>
          {moduleRows.map((row) => (
            <tr key={row.name}>
              <td>{row.name}</td>
              <td>{row.created}</td>
              <td>{row.pending}</td>
              <td>{row.abnormal}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
"""

ENV_DEVELOPMENT = """\
VITE_API_BASE=
VITE_APP_NAME=__APP_NAME__
"""


# ---------------------------------------------------------------------------
# 纯前端（backend = none）：本地数据层 + 不接后端的页面
#
# 与全栈版的差别只有两处：vite 不再代理 /api，页面也不再发 HTTP，改成直接调
# src/api/local-service.ts。数据用示例数据播种，写进浏览器 localStorage。
# 这样「纯前端」不是把后端删掉留个空壳，而是换了一条真实的本地数据链路。
# ---------------------------------------------------------------------------
VITE_CONFIG_VUE_LOCAL = """\
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 纯前端应用：没有后端，也就没有 /api 代理，数据全部走 src/api/local-service.ts。
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: __FRONTEND_PORT__,
    // 关掉自动打开页面：起服务时只打印地址，不拉起浏览器
    open: false,
    strictPort: false,
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
"""

VITE_CONFIG_REACT_LOCAL = """\
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 纯前端应用：没有后端，也就没有 /api 代理，数据全部走 src/api/local-service.ts。
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: __FRONTEND_PORT__,
    // 关掉自动打开页面：起服务时只打印地址，不拉起浏览器
    open: false,
    strictPort: false,
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
"""

LOCAL_TYPES_TS = """\
/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}
"""

LOCAL_MODULES_TS = """\
import type { ModuleMeta } from './types'

// 模块元数据由仓库生成时写入：字段、状态、动作、流转目标都在这里，页面不再各自写一遍。
__MODULE_BLOCKS__

export const MODULE_BY_KEY: Map<string, ModuleMeta> = new Map(
  MODULES.map((item) => [item.key, item]),
)
"""

LOCAL_SEED_TS = """\
import type { EntryRow } from './types'

// 示例数据：首次打开时播种，之后浏览器里的改动优先，重置才会回到这份。
__SEED_BLOCKS__
"""

LOCAL_STORE_TS = """\
import { SEED_ROWS } from './seed'
import type { EntryRow } from './types'

// 本地持久化：数据放在 localStorage 里，刷新、关掉再打开都还在。
const STORAGE_KEY = '__APP_SLUG__:entries'

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function readStorage(): Record<string, EntryRow[]> {
  const fallback = clone(SEED_ROWS)
  if (typeof window === 'undefined' || !window.localStorage) {
    return fallback
  }
  const raw = window.localStorage.getItem(STORAGE_KEY)
  if (!raw) {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(fallback))
    return fallback
  }
  try {
    const parsed = JSON.parse(raw) as Record<string, EntryRow[]>
    return { ...fallback, ...parsed }
  } catch {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(fallback))
    return fallback
  }
}

let cache: Record<string, EntryRow[]> | null = null

export function allRows(): Record<string, EntryRow[]> {
  if (cache === null) {
    cache = readStorage()
  }
  return cache
}

export function listRows(key: string): EntryRow[] {
  return allRows()[key] ?? []
}

export function saveRows(key: string, rows: EntryRow[]): void {
  const next = { ...allRows(), [key]: rows }
  cache = next
  if (typeof window !== 'undefined' && window.localStorage) {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  }
}

export function resetRows(key: string): EntryRow[] {
  const rows = clone(SEED_ROWS[key] ?? [])
  saveRows(key, rows)
  return rows
}

export function storageKey(): string {
  return STORAGE_KEY
}
"""

LOCAL_SERVICE_TS = """\
import { MODULE_BY_KEY } from '@/data/modules'
import { allRows, listRows, resetRows, saveRows } from '@/data/local-store'
import type { ActionResult, EntryRow, ModuleMeta, OverviewResult, PageResult } from '@/data/types'

// 会写进数据的「往回走」动作：命中就把这条记录标成异常态，看板上能一眼看出来。
const NEGATIVE_ACTIONS = ['撤销', '作废', '拒绝', '驳回', '停用', '忽略', '下线', '回滚']

export function moduleMeta(key: string): ModuleMeta {
  const meta = MODULE_BY_KEY.get(key)
  if (!meta) {
    throw new Error(`没有登记名为 ${key} 的业务模块`)
  }
  return meta
}

export function filterRows(rows: EntryRow[], filters: Record<string, string>): EntryRow[] {
  const pairs = Object.entries(filters).filter(([, value]) => value.trim() !== '')
  if (pairs.length === 0) {
    return rows
  }
  return rows.filter((row) =>
    pairs.every(([field, value]) => String(row[field] ?? '').includes(value.trim())),
  )
}

export function listEntries(key: string, filters: Record<string, string> = {}): PageResult {
  const matched = filterRows(listRows(key), filters)
  return { items: matched, total: matched.length, page: 1, size: matched.length }
}

export function runAction(key: string, id: number, action: string): ActionResult {
  const meta = moduleMeta(key)
  const target = meta.actionTargets[action]
  if (!target) {
    return { ok: false, message: `${meta.entity}没有登记「${action}」这个动作` }
  }
  const rows = listRows(key)
  const index = rows.findIndex((row) => Number(row.id) === id)
  if (index < 0) {
    return { ok: false, message: `没有找到编号为 ${id} 的${meta.entity}` }
  }
  const current = String(rows[index].status)
  if (current === target) {
    return { ok: false, message: `${meta.entity}已经是「${target}」，不用重复操作` }
  }
  const lastStatus = meta.statuses[meta.statuses.length - 1]
  const updated: EntryRow = {
    ...rows[index],
    status: target,
    pending: target !== lastStatus,
    abnormal: NEGATIVE_ACTIONS.some((verb) => action.startsWith(verb)),
  }
  const next = [...rows]
  next[index] = updated
  saveRows(key, next)
  return { ok: true, message: `${meta.entity}已${action}，当前状态「${target}」` }
}

export function resetModule(key: string): PageResult {
  resetRows(key)
  return listEntries(key)
}

export function exportEntries(key: string): { filename: string; content: string } {
  const meta = moduleMeta(key)
  const header = ['编号', ...meta.fields, '当前状态']
  const lines = [header.join(',')]
  for (const row of listRows(key)) {
    lines.push([row.id, ...meta.fields.map((field) => row[field] ?? ''), row.status].join(','))
  }
  return { filename: `${meta.name}-清单.csv`, content: `\\uFEFF${lines.join('\\n')}` }
}

export function downloadEntries(key: string): void {
  const { filename, content } = exportEntries(key)
  const blob = new Blob([content], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export function loadOverview(): OverviewResult {
  const rows = allRows()
  const modules = [...MODULE_BY_KEY.values()].map((meta) => {
    const entries = rows[meta.key] ?? []
    return {
      name: meta.name,
      created: entries.length,
      pending: entries.filter((row) => row.pending).length,
      abnormal: entries.filter((row) => row.abnormal).length,
    }
  })
  const cards = [
    { label: '业务模块', value: modules.length },
    { label: '登记总量', value: modules.reduce((sum, item) => sum + item.created, 0) },
    { label: '待处理', value: modules.reduce((sum, item) => sum + item.pending, 0) },
    { label: '异常量', value: modules.reduce((sum, item) => sum + item.abnormal, 0) },
  ]
  return { cards, modules }
}
"""

VUE_VIEW_LOCAL = """\
<template>
  <section class="page" data-module="__KEY__">
    <header class="page-head">
      <div>
        <h2>__NAME__管理</h2>
        <p class="page-desc">__DESC__</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记__ENTITY__</button>
        <button class="btn" type="button" @click="exportRows">导出__NAME__清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无__NAME__数据，可先登记__ENTITY__</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条__NAME__记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  downloadEntries,
  listEntries,
  moduleMeta,
  runAction as applyAction,
} from '@/api/local-service'
import type { EntryRow } from '@/data/types'

const meta = moduleMeta('__KEY__')
const columns = __FIELDS__
const actions = __ACTIONS__
const statuses = __STATUSES__
const stats = __METRICS__

const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const statusSummary = computed(() =>
  statuses.map((status: string) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)

function resetFilters() {
  filters.value = {}
  reload()
}

function exportRows() {
  downloadEntries(meta.key)
}

function openCreate() {
  errorMessage.value = '__ENTITY__登记入口尚未接入审批流'
}

function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  const result = applyAction(meta.key, Number(row.id), action)
  if (!result.ok) {
    errorMessage.value = result.message
    return
  }
  reload()
}

function reload() {
  errorMessage.value = ''
  try {
    const payload = listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '__NAME__列表读取失败'
  }
}

onMounted(reload)
</script>
"""

REACT_VIEW_LOCAL = """\
import { useCallback, useEffect, useMemo, useState } from 'react'

import { downloadEntries, listEntries, moduleMeta, runAction as applyAction } from '@/api/local-service'
import type { EntryRow } from '@/data/types'

const meta = moduleMeta('__KEY__')
const columns = __FIELDS__
const actions = __ACTIONS__
const statuses = __STATUSES__
const stats = __METRICS__

export default function __CLASS__View() {
  const [rows, setRows] = useState<EntryRow[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(() => {
    setErrorMessage('')
    try {
      const payload = listEntries(meta.key, filters)
      setRows(payload.items)
      setTotal(payload.total)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '__NAME__列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    reload()
  }, [reload])

  function runAction(action: string, row: EntryRow) {
    setErrorMessage('')
    const result = applyAction(meta.key, Number(row.id), action)
    if (!result.ok) {
      setErrorMessage(result.message)
      return
    }
    reload()
  }

  return (
    <section className="page" data-module="__KEY__">
      <header className="page-head">
        <div>
          <h2>__NAME__管理</h2>
          <p className="page-desc">__DESC__</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('__ENTITY__登记入口尚未接入审批流')}>
            登记__ENTITY__
          </button>
          <button className="btn" type="button" onClick={() => downloadEntries(meta.key)}>
            导出__NAME__清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <p className="status-legend">
        {statuses.map((status: string) => (
          <span key={status} className="legend-item">
            {status}：{rows.filter((row) => String(row.status) === status).length}
          </span>
        ))}
      </p>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>当前状态</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td>{row.status}</td>
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>暂无__NAME__数据，可先登记__ENTITY__</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条__NAME__记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
"""

DASHBOARD_VUE_LOCAL = """\
<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>运营概览</h2>
        <p class="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="refresh">重新统计</button>
      </div>
    </header>
    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>
    <table class="data-table">
      <thead>
        <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in moduleRows" :key="row.name">
          <td>{{ row.name }}</td>
          <td>{{ row.created }}</td>
          <td>{{ row.pending }}</td>
          <td>{{ row.abnormal }}</td>
        </tr>
      </tbody>
    </table>
    <footer class="page-foot">
      <span>数据保存在本机浏览器里，换浏览器或清缓存会回到示例数据</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { loadOverview } from '@/api/local-service'
import type { OverviewResult } from '@/data/types'

const cards = ref<OverviewResult['cards']>([])
const moduleRows = ref<OverviewResult['modules']>([])

function refresh() {
  const payload = loadOverview()
  cards.value = payload.cards
  moduleRows.value = payload.modules
}

onMounted(refresh)
</script>
"""

DASHBOARD_REACT_LOCAL = """\
import { useCallback, useEffect, useState } from 'react'

import { loadOverview } from '@/api/local-service'
import type { OverviewResult } from '@/data/types'

export default function Dashboard() {
  const [cards, setCards] = useState<OverviewResult['cards']>([])
  const [moduleRows, setModuleRows] = useState<OverviewResult['modules']>([])

  const refresh = useCallback(() => {
    const payload = loadOverview()
    setCards(payload.cards)
    setModuleRows(payload.modules)
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h2>运营概览</h2>
          <p className="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={refresh}>重新统计</button>
        </div>
      </header>
      <div className="stat-row">
        {cards.map((card) => (
          <article key={card.label} className="stat-card">
            <span className="stat-label">{card.label}</span>
            <strong className="stat-value">{card.value}</strong>
          </article>
        ))}
      </div>
      <table className="data-table">
        <thead>
          <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
        </thead>
        <tbody>
          {moduleRows.map((row) => (
            <tr key={row.name}>
              <td>{row.name}</td>
              <td>{row.created}</td>
              <td>{row.pending}</td>
              <td>{row.abnormal}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <footer className="page-foot">
        <span>数据保存在本机浏览器里，换浏览器或清缓存会回到示例数据</span>
      </footer>
    </section>
  )
}
"""

LOCAL_CSS_EXTRA = """\
.status-legend { display: flex; flex-wrap: wrap; gap: 12px; margin: 0 0 10px; font-size: 12px; color: var(--muted); }
.legend-item { background: #eef2f7; border-radius: 999px; padding: 2px 10px; }
"""

README_LOCAL = """\
# __APP_NAME__

__APP_DESC__

这是一个**纯前端**管理平台：__FRONTEND_LABEL__，仓库里没有后端服务。业务数据由
`frontend/src/data/` 下的本地数据层提供：首次打开用示例数据播种，之后的登记、筛选与状态流转
结果都持久化在浏览器 `localStorage` 里，刷新或重开浏览器都还在。dev server 已关掉自动打开页面，
启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 __FRONTEND_LABEL__ 前端（唯一运行单元）
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/local-service.ts   本地数据服务：列表、筛选、动作流转、导出
│   ├── src/data/             模块元数据 / 示例数据 / localStorage 持久化
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false，无 /api 代理）
├── .gitignore
└── docker-compose.yml
```

## 启动

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:__FRONTEND_PORT__/`，dev server 不会自动打开浏览器，需要自己访问。

生产构建：

```bash
cd frontend
npm run build
```

## 业务模块

__MODULE_TABLE__

## 约定

- 每个模块的页面在 `frontend/src/views/<模块>/index.vue`，页面只负责渲染，读写统一走
  `frontend/src/api/local-service.ts`。
- 字段、状态、动作与流转目标集中在 `frontend/src/data/modules.ts`；示例数据在
  `frontend/src/data/seed.ts`。
- 状态流转只允许在 `local-service.ts` 里改，页面组件不做业务判断。
- 想回到初始数据：清掉浏览器里 `__STORAGE_KEY__` 这一项，或调用 `resetModule(模块)`。
"""

COMPOSE_LOCAL = """\
services:
  frontend:
    build: ./frontend
    ports:
      - "__FRONTEND_PORT__:__FRONTEND_PORT__"
"""

MAKEFILE_LOCAL = """\
.PHONY: install frontend build

install:
\tcd frontend && npm install

frontend:
\tcd frontend && npm run dev

build:
\tcd frontend && npm run build
"""


# ---------------------------------------------------------------------------
# 后端：Python（FastAPI）
# ---------------------------------------------------------------------------
REQUIREMENTS = """\
fastapi>=0.110
uvicorn[standard]>=0.29
pydantic>=2.6
"""

BACKEND_MAIN = '''\
"""__APP_NAME__ 后端服务入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port __BACKEND_PORT__
健康检查：GET /api/health
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import ROUTERS
from app.store import store

app = FastAPI(title="__APP_NAME__", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in ROUTERS:
    app.include_router(module.router)


@app.get("/api/health")
def health() -> dict[str, object]:
    """健康检查：确认服务已经监听、示例数据已经就绪。"""
    return {"ok": True, "app": settings.app_name, "modules": len(store.module_names())}


@app.get("/api/overview")
def overview() -> dict[str, object]:
    """运营概览：把各业务模块的待处理量汇总成看板卡片。"""
    return store.overview()
'''

ROUTERS_INIT = '''\
"""业务模块路由汇总。

这里统一按别名导入再暴露 ROUTERS：模块名有可能和内置名撞车（某个业务模块就叫 dict、list
这种名字时），按名字直接 import 会把内置类型覆盖掉，函数注解在运行时求值就会报
'module' object is not subscriptable。
"""
from __future__ import annotations

__IMPORTS__

ROUTERS = [__MODULES__]
'''

BACKEND_CONFIG = '''\
"""运行配置：端口、跨域、运行环境。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    app_name: str = "__APP_NAME__"
    env: str = "local"
    port: int = __BACKEND_PORT__
    allowed_origins: list[str] = field(
        default_factory=lambda: [
            "http://127.0.0.1:__FRONTEND_PORT__",
            "http://localhost:__FRONTEND_PORT__",
        ]
    )
    page_size_default: int = 20
    page_size_max: int = 200


settings = Settings()
'''

BACKEND_STORE = '''\
"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

from typing import Any

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
'''

BACKEND_SCHEMAS = '''\
"""接口出入参模型：列表分页、动作结果与各模块的明细结构。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    """登记或修改一条业务记录时提交的字段集合。"""

    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None

__SCHEMAS__
'''

ROUTER_PY = '''\
"""__NAME__接口：维护__ENTITY__，覆盖__ACTION_JOIN__等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.__KEY__ import __CLASS__Service

router = APIRouter(prefix="/api/__KEY__", tags=["__NAME__"])

service = __CLASS__Service()

LIST_FIELDS = __FIELDS__
STATUSES = __STATUSES__


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按__FIELD0__检索"),
    status: str | None = Query(default=None, description="__STATUS_JOIN__"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按__FIELD0__与状态过滤__NAME__列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条__ENTITY__明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"__ENTITY__ {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条__ENTITY__，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="__ENTITY__已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条__ENTITY__执行__ACTION_JOIN__；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出__NAME__清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "__KEY__", "total": total, "items": items}
'''

SERVICE_PY = '''\
"""__NAME__业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "__KEY__"
REQUIRED_FIELDS = __REQUIRED_FIELDS__
STATUS_ORDER = __STATUSES__
ACTION_RULES = __ACTION_RULES__
NEGATIVE_ACTIONS = __NEGATIVE_ACTIONS__


class __CLASS__Service:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("__FIELD0__", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"__ENTITY__ {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于__NAME__可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"__ENTITY__已{action}"
'''

BACKEND_SEED = '''\
"""示例数据：每个模块给几条不同状态的记录，方便起服务后立刻看到内容。"""
from __future__ import annotations

from typing import Any

SEED_ROWS: dict[str, list[dict[str, Any]]] = {
__SEED_TABLE__
}
'''

BACKEND_RUN_SH = """\
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port __BACKEND_PORT__
"""

JAVA_RUN_SH = """\
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v mvn >/dev/null 2>&1; then
  echo "需要 JDK 17 与 Maven：请先安装后再启动后端" >&2
  exit 1
fi
exec mvn -q spring-boot:run
"""

BACKEND_DOCKERFILE = """\
FROM python:3.12-slim
WORKDIR /srv/app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE __BACKEND_PORT__
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "__BACKEND_PORT__"]
"""

BACKEND_DOCKERFILE_JAVA = """\
FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /srv/app
COPY pom.xml ./
COPY src ./src
RUN mvn -q -DskipTests package

FROM eclipse-temurin:17-jre
WORKDIR /srv/app
COPY --from=build /srv/app/target/*.jar app.jar
EXPOSE __BACKEND_PORT__
CMD ["java", "-jar", "app.jar"]
"""


# ---------------------------------------------------------------------------
# 后端：Java（Spring Boot）
# ---------------------------------------------------------------------------
JAVA_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.2.5</version>
    <relativePath/>
  </parent>
  <groupId>com.example</groupId>
  <artifactId>__APP_SLUG__-backend</artifactId>
  <version>1.0.0</version>
  <name>__APP_NAME__ backend</name>
  <properties>
    <java.version>17</java.version>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-validation</artifactId>
    </dependency>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-test</artifactId>
      <scope>test</scope>
    </dependency>
  </dependencies>
  <build>
    <plugins>
      <plugin>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-maven-plugin</artifactId>
      </plugin>
    </plugins>
  </build>
</project>
"""

JAVA_APPLICATION = """\
package com.example.__PKG__;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/** __APP_NAME__ 后端服务入口：默认监听 __BACKEND_PORT__。 */
@SpringBootApplication
public class Application {

    public static void main(String[] args) {
        SpringApplication.run(Application.class, args);
    }
}
"""

JAVA_CONTROLLER = """\
package com.example.__PKG__.controller;

import com.example.__PKG__.service.__CLASS__Service;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/** __NAME__接口：维护__ENTITY__，覆盖__ACTION_JOIN__等动作。 */
@RestController
@RequestMapping("/api/__KEY__")
public class __CLASS__Controller {

    private final __CLASS__Service service;

    public __CLASS__Controller(__CLASS__Service service) {
        this.service = service;
    }

    /** 按__FIELD0__与状态过滤__NAME__列表；没有数据时返回空列表，不抛异常。 */
    @GetMapping
    public Map<String, Object> list(
            @RequestParam(required = false) String keyword,
            @RequestParam(required = false) String status,
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "20") int size) {
        if (size > 200) {
            throw new IllegalArgumentException("每页最多 200 条，请缩小分页范围");
        }
        List<Map<String, Object>> items = service.list(keyword, status, page, size);
        return Map.of("items", items, "total", service.count(keyword, status), "page", page, "size", size);
    }

    /** 读取单条__ENTITY__明细；不存在时给出可读的错误说明。 */
    @GetMapping("/{id}")
    public Map<String, Object> detail(@PathVariable long id) {
        return service.find(id)
                .map(row -> Map.<String, Object>of("ok", true, "entry", row))
                .orElseGet(() -> Map.of("ok", false, "message", "__ENTITY__不存在或已归档"));
    }

    /** 登记一条__ENTITY__，缺字段时说明原因而不是静默丢弃。 */
    @PostMapping
    public Map<String, Object> create(@RequestBody Map<String, Object> values) {
        List<String> missing = service.validate(values);
        if (!missing.isEmpty()) {
            return Map.of("ok", false, "message", "缺少必填字段：" + String.join("、", missing));
        }
        return Map.of("ok", true, "message", "__ENTITY__已登记", "entry", service.create(values));
    }

    /** 对单条__ENTITY__执行__ACTION_JOIN__；不允许的动作会被拦下并说明原因。 */
    @PostMapping("/{id}/actions")
    public Map<String, Object> runAction(@PathVariable long id, @RequestBody Map<String, Object> payload) {
        String action = String.valueOf(payload.getOrDefault("action", ""));
        return service.runAction(id, action);
    }

    /** 导出__NAME__清单：返回当前过滤条件下的全量数据。 */
    @GetMapping("/export")
    public Map<String, Object> export() {
        List<Map<String, Object>> items = service.list(null, null, 1, 10000);
        return Map.of("module", "__KEY__", "total", items.size(), "items", items);
    }
}
"""

JAVA_SERVICE = """\
package com.example.__PKG__.service;

import org.springframework.stereotype.Service;

import java.util.*;

/** __NAME__业务规则：状态流转、字段校验与筛选口径都收在这里。 */
@Service
public class __CLASS__Service implements ModuleService {

    private static final List<String> REQUIRED_FIELDS = List.of(__REQUIRED_FIELDS__);
    private static final List<String> STATUS_ORDER = List.of(__STATUSES__);
    private static final Map<String, String> ACTION_RULES = Map.of(__ACTION_RULES__);
    private static final List<String> NEGATIVE_ACTIONS = List.of(__NEGATIVE_ACTIONS__);

    private final List<Map<String, Object>> rows = new ArrayList<>();
    private long sequence = 0L;

    public __CLASS__Service() {
        seed();
    }

    /** 模块中文名，概览页按它给每一行命名。 */
    @Override
    public String label() {
        return "__NAME__";
    }

    private void seed() {
        for (int index = 0; index < 3; index++) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", ++sequence);
            for (String field : REQUIRED_FIELDS) {
                row.put(field, field + "样例" + (index + 1));
            }
            row.put("status", STATUS_ORDER.get(Math.min(index, STATUS_ORDER.size() - 1)));
            row.put("pending", index < 2);
            row.put("abnormal", index == 1);
            rows.add(row);
        }
    }

    public List<Map<String, Object>> list(String keyword, String status, int page, int size) {
        List<Map<String, Object>> filtered = filter(keyword, status);
        int from = Math.max(0, (page - 1) * size);
        int to = Math.min(filtered.size(), from + size);
        if (from >= to) {
            return List.of();
        }
        return filtered.subList(from, to);
    }

    @Override
    public int count(String keyword, String status) {
        return filter(keyword, status).size();
    }

    private List<Map<String, Object>> filter(String keyword, String status) {
        List<Map<String, Object>> filtered = new ArrayList<>();
        for (Map<String, Object> row : rows) {
            if (status != null && !status.equals(row.get("status"))) {
                continue;
            }
            if (keyword != null && !String.valueOf(row.get(REQUIRED_FIELDS.get(0))).contains(keyword)) {
                continue;
            }
            filtered.add(row);
        }
        return filtered;
    }

    public Optional<Map<String, Object>> find(long id) {
        return rows.stream().filter(row -> Objects.equals(row.get("id"), id)).findFirst();
    }

    public List<String> validate(Map<String, Object> values) {
        List<String> missing = new ArrayList<>();
        for (String field : REQUIRED_FIELDS) {
            Object value = values.get(field);
            if (value == null || String.valueOf(value).trim().isEmpty()) {
                missing.add(field);
            }
        }
        return missing;
    }

    public Map<String, Object> create(Map<String, Object> values) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("id", ++sequence);
        for (String field : REQUIRED_FIELDS) {
            row.put(field, values.get(field));
        }
        row.put("status", STATUS_ORDER.get(0));
        row.put("pending", true);
        row.put("abnormal", false);
        rows.add(row);
        return row;
    }

    public Map<String, Object> runAction(long id, String action) {
        Optional<Map<String, Object>> found = find(id);
        if (found.isEmpty()) {
            return Map.of("ok", false, "message", "__ENTITY__不存在或已归档");
        }
        if (!ACTION_RULES.containsKey(action)) {
            return Map.of("ok", false, "message", "动作「" + action + "」不属于__NAME__可执行范围");
        }
        Map<String, Object> row = found.get();
        String target = ACTION_RULES.get(action);
        row.put("status", target);
        row.put("pending", !target.equals(STATUS_ORDER.get(STATUS_ORDER.size() - 1)));
        row.put("abnormal", NEGATIVE_ACTIONS.contains(action));
        return Map.of("ok", true, "message", "__ENTITY__已" + action, "entry", row);
    }
}
"""

JAVA_OVERVIEW = """\
package com.example.__PKG__.controller;

import com.example.__PKG__.service.OverviewService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/** 运营概览接口：把各业务模块的待处理量汇总成看板卡片。 */
@RestController
public class OverviewController {

    private final OverviewService overviewService;

    public OverviewController(OverviewService overviewService) {
        this.overviewService = overviewService;
    }

    /** 健康检查：确认服务已经监听、示例数据已经就绪。 */
    @GetMapping("/api/health")
    public Map<String, Object> health() {
        return Map.of("ok", true, "app", "__APP_NAME__", "modules", overviewService.moduleCount());
    }

    /** 看板卡片 + 各模块明细。 */
    @GetMapping("/api/overview")
    public Map<String, Object> overview() {
        return overviewService.overview();
    }
}
"""

JAVA_MODULE_SERVICE = """\
package com.example.__PKG__.service;

/** 各业务模块共用的能力：给出模块名与记录条数，供概览页汇总。 */
public interface ModuleService {

    /** 模块中文名。 */
    String label();

    /** 按关键字与状态统计记录条数。 */
    int count(String keyword, String status);
}
"""

JAVA_OVERVIEW_SERVICE = """\
package com.example.__PKG__.service;

import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** 汇总各业务模块的关键指标，供运营概览页使用。 */
@Service
public class OverviewService {

    private final List<ModuleService> moduleServices;

    public OverviewService(List<ModuleService> moduleServices) {
        this.moduleServices = moduleServices;
    }

    public int moduleCount() {
        return moduleServices.size();
    }

    public Map<String, Object> overview() {
        List<Map<String, Object>> modules = new ArrayList<>();
        int created = 0;
        for (ModuleService service : moduleServices) {
            int rows = service.count(null, null);
            created += rows;
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("name", service.label());
            item.put("created", rows);
            item.put("pending", 0);
            item.put("abnormal", 0);
            modules.add(item);
        }
        return Map.of(
                "cards", List.of(
                        Map.of("label", "业务模块", "value", moduleServices.size()),
                        Map.of("label", "今日新增", "value", created)),
                "modules", modules);
    }
}
"""

JAVA_APP_YML = """\
server:
  port: __BACKEND_PORT__
spring:
  application:
    name: __APP_SLUG__-backend
logging:
  level:
    root: INFO
"""

FRONTEND_DOCKERFILE = """\
FROM node:20-alpine
WORKDIR /srv/web
COPY package.json ./
RUN npm install
COPY . .
EXPOSE __FRONTEND_PORT__
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
"""


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------
def module_desc(module: dict) -> str:
    if module.get("desc"):
        return module["desc"]
    fields = "、".join(module["fields"][:4])
    return f"维护{module['entity']}，围绕{fields}做登记、筛选与状态流转。"


def module_metrics(module: dict) -> str:
    metrics = module.get("metrics")
    if not metrics:
        metrics = [f"累计{module['name']}", f"待处理{module['name']}", f"异常{module['name']}"]
    return metrics


# 命中这些动词的动作会把记录标记成异常态，方便页面与看板区分「正常推进」和「被打回」。
NEGATIVE_ACTION_VERBS = ("撤销", "作废", "拒绝", "驳回", "停用", "忽略", "下线", "回滚", "失败")


def sample_value(field: str, module_key: str, module_name: str, index: int) -> object:
    """按字段名造一条像样的示例值，别让「联系电话」里躺着业务名称。"""
    number = index + 1
    if any(token in field for token in ("电话", "手机", "联系方式")):
        return f"1380000{number:04d}"
    if any(token in field for token in ("时间", "日期", "期限", "时段", "投运")):
        return f"2026-09-{number:02d}"
    if "邮箱" in field:
        return f"operator{number}@example.com"
    if any(token in field for token in ("数量", "库存", "排序号")):
        return number * 10
    if any(token in field for token in ("金额", "价格", "费用")):
        return round(number * 12.5, 2)
    if any(token in field for token in ("编码", "编号", "账号", "单号", "证号")):
        return f"{module_key.upper()[:4]}-{number:04d}"
    return f"{module_name}样例{number}"


def module_actions(module: dict) -> dict[str, object]:
    """模块的动作、状态与状态流转表；流转目标在 spec 里显式给出，缺省按状态顺序推进。"""
    actions = list(module.get("actions") or [f"提交{module['name']}"])
    statuses = list(module.get("statuses") or ["待处理", "处理中", "已完成"])
    explicit = module.get("action_targets")
    if explicit:
        if len(explicit) != len(actions):
            raise SystemExit(
                f"模块 {module['key']} 的 action_targets 数量与 actions 不一致（{len(explicit)} != {len(actions)}）")
        targets = list(explicit)
    else:
        targets = [statuses[min(index + 1, len(statuses) - 1)] for index in range(len(actions))]
    unknown = [target for target in targets if target not in statuses]
    if unknown:
        raise SystemExit(f"模块 {module['key']} 的 action_targets 出现未定义状态：{unknown}")
    rules = {action: target for action, target in zip(actions, targets)}
    negative = [action for action in actions if action.startswith(NEGATIVE_ACTION_VERBS)]
    return {
        "actions": actions,
        "statuses": statuses,
        "rules": rules,
        "negative": negative,
    }


def build_nav_items(modules: list[dict]) -> str:
    items = [{"label": "运营概览", "path": "/"}]
    for module in modules:
        items.append({"label": module["name"], "path": f"/{module['key']}"})
    body = ", ".join(
        "{ label: " + json.dumps(item["label"], ensure_ascii=False)
        + ", path: " + json.dumps(item["path"]) + " }"
        for item in items
    )
    return f"[{body}]"


def indent_block(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else line for line in text.splitlines())


def build_local_module_blocks(modules: list[dict]) -> str:
    """纯前端版的模块元数据：字段 / 状态 / 动作 / 流转目标都落进 modules.ts。"""
    blocks: list[str] = []
    for module in modules:
        flow = module_actions(module)
        blocks.append(
            "  {\n"
            f"    key: {json.dumps(module['key'])},\n"
            f"    name: {json.dumps(module['name'], ensure_ascii=False)},\n"
            f"    entity: {json.dumps(module['entity'], ensure_ascii=False)},\n"
            f"    desc: {json.dumps(module_desc(module), ensure_ascii=False)},\n"
            f"    fields: {ts_array(module['fields'])},\n"
            f"    statuses: {ts_array(flow['statuses'])},\n"
            f"    actions: {ts_array(flow['actions'])},\n"
            f"    actionTargets: {json.dumps(flow['rules'], ensure_ascii=False)},\n"
            f"    metrics: {ts_array(module_metrics(module))},\n"
            "  },"
        )
    return "export const MODULES: ModuleMeta[] = [\n" + "\n".join(blocks) + "\n]"


def build_local_seed_blocks(modules: list[dict]) -> str:
    """纯前端版的示例数据：每个模块三行，字段值与后端 seed 用同一套采样口径。"""
    blocks: list[str] = []
    for module in modules:
        statuses = module_actions(module)["statuses"]
        rows: list[dict] = []
        for index in range(3):
            row: dict[str, object] = {
                "id": index + 1,
                "status": statuses[index % len(statuses)],
                "pending": index < 2,
                "abnormal": index == 1,
            }
            for field in module["fields"]:
                row[field] = sample_value(field, module["key"], module["name"], index)
            rows.append(row)
        body = indent_block(json.dumps(rows, ensure_ascii=False, indent=2), 2)
        head, _, tail = body.partition("\n")
        blocks.append(f"  {json.dumps(module['key'])}: {head.lstrip()}\n{tail},")
    return "export const SEED_ROWS: Record<string, EntryRow[]> = {\n" + "\n".join(blocks) + "\n}"


def gen_frontend_vue(spec: dict, out: Path) -> int:
    modules = spec["modules"]
    frontend_only = spec["backend"] == "none"
    fe = out / "frontend"
    lines = 0
    files: dict[Path, str] = {}
    files[fe / "package.json"] = render(
        PACKAGE_JSON_VUE, APP_SLUG=spec["app_slug"], FRONTEND_PORT=spec["port_frontend"],
        REPO_KIND="frontend-only" if frontend_only else "fullstack")
    files[fe / "vite.config.ts"] = (
        render(VITE_CONFIG_VUE_LOCAL, FRONTEND_PORT=spec["port_frontend"]) if frontend_only
        else render(VITE_CONFIG_VUE, FRONTEND_PORT=spec["port_frontend"],
                    BACKEND_PORT=spec["port_backend"]))
    files[fe / "index.html"] = render(
        INDEX_HTML, APP_NAME=spec["app_name"], MAIN_EXT="ts")
    files[fe / "tsconfig.json"] = TSCONFIG_VUE
    files[fe / ".env.development"] = render(ENV_DEVELOPMENT, APP_NAME=spec["app_name"])
    files[fe / "Dockerfile"] = render(
        FRONTEND_DOCKERFILE, FRONTEND_PORT=spec["port_frontend"])
    files[fe / "src/main.ts"] = MAIN_VUE
    files[fe / "src/env.d.ts"] = ENV_D_TS
    files[fe / "src/App.vue"] = render(
        APP_VUE, APP_NAME=spec["app_name"], APP_DESC=spec["app_desc"],
        NAV_ITEMS=build_nav_items(modules))
    files[fe / "src/stores/session.ts"] = render(SESSION_STORE, APP_SCOPE=spec["app_name"])
    files[fe / "src/styles/global.css"] = (
        GLOBAL_CSS + LOCAL_CSS_EXTRA if frontend_only else GLOBAL_CSS)

    if frontend_only:
        files[fe / "src/data/types.ts"] = LOCAL_TYPES_TS
        files[fe / "src/data/modules.ts"] = render(
            LOCAL_MODULES_TS, MODULE_BLOCKS=build_local_module_blocks(modules))
        files[fe / "src/data/seed.ts"] = render(
            LOCAL_SEED_TS, SEED_BLOCKS=build_local_seed_blocks(modules))
        files[fe / "src/data/local-store.ts"] = render(
            LOCAL_STORE_TS, APP_SLUG=spec["app_slug"])
        files[fe / "src/api/local-service.ts"] = LOCAL_SERVICE_TS
    else:
        files[fe / "src/api/client.ts"] = CLIENT_TS

    view_imports = ["import Dashboard from '@/views/Dashboard.vue'"]
    routes = []
    for module in modules:
        key = module["key"]
        cls = class_name(key)
        view_imports.append(f"const {cls} = () => import('@/views/{key}/index.vue')")
        routes.append(
            "    { path: '" + f"/{key}" + "', name: '" + key + "', component: " + cls + " },"
        )
        files[fe / f"src/views/{key}/index.vue"] = render(
            VUE_VIEW_LOCAL if frontend_only else VUE_VIEW,
            KEY=key, NAME=module["name"], ENTITY=module["entity"],
            DESC=module_desc(module),
            FIELDS=ts_array(module["fields"]),
            ACTIONS=ts_array(module.get("actions") or [f"提交{module['name']}"]),
            STATUSES=ts_array(module.get("statuses") or ["待处理", "处理中", "已完成"]),
            METRICS=json.dumps(
                [{"label": label, "value": 0} for label in module_metrics(module)],
                ensure_ascii=False),
        )
    files[fe / "src/router/index.ts"] = render(
        ROUTER_TS, VIEW_IMPORTS="\n".join(view_imports), ROUTES="\n".join(routes))

    fallback_cards = json.dumps(
        [{"label": "业务模块", "value": 0}, {"label": "今日新增", "value": 0}], ensure_ascii=False)
    fallback_modules = json.dumps(
        [{"name": module["name"], "created": 0, "pending": 0, "abnormal": 0} for module in modules],
        ensure_ascii=False)
    files[fe / "src/views/Dashboard.vue"] = (
        DASHBOARD_VUE_LOCAL if frontend_only
        else render(DASHBOARD_VUE, FALLBACK_CARDS=fallback_cards, FALLBACK_MODULES=fallback_modules))

    for path, content in files.items():
        write(path, content)
        lines += line_count(content)
    return lines


def gen_frontend_react(spec: dict, out: Path) -> int:
    modules = spec["modules"]
    frontend_only = spec["backend"] == "none"
    fe = out / "frontend"
    lines = 0
    files: dict[Path, str] = {}
    files[fe / "package.json"] = render(
        PACKAGE_JSON_REACT, APP_SLUG=spec["app_slug"], FRONTEND_PORT=spec["port_frontend"],
        REPO_KIND="frontend-only" if frontend_only else "fullstack")
    files[fe / "vite.config.ts"] = (
        render(VITE_CONFIG_REACT_LOCAL, FRONTEND_PORT=spec["port_frontend"]) if frontend_only
        else render(VITE_CONFIG_REACT, FRONTEND_PORT=spec["port_frontend"],
                    BACKEND_PORT=spec["port_backend"]))
    files[fe / "index.html"] = render(INDEX_HTML, APP_NAME=spec["app_name"], MAIN_EXT="tsx")
    files[fe / "tsconfig.json"] = TSCONFIG_REACT
    files[fe / ".env.development"] = render(ENV_DEVELOPMENT, APP_NAME=spec["app_name"])
    files[fe / "Dockerfile"] = render(
        FRONTEND_DOCKERFILE, FRONTEND_PORT=spec["port_frontend"])
    files[fe / "src/main.tsx"] = MAIN_REACT
    files[fe / "src/env.d.ts"] = ENV_D_TS
    files[fe / "src/styles/global.css"] = (
        GLOBAL_CSS + LOCAL_CSS_EXTRA if frontend_only else GLOBAL_CSS)

    if frontend_only:
        files[fe / "src/data/types.ts"] = LOCAL_TYPES_TS
        files[fe / "src/data/modules.ts"] = render(
            LOCAL_MODULES_TS, MODULE_BLOCKS=build_local_module_blocks(modules))
        files[fe / "src/data/seed.ts"] = render(
            LOCAL_SEED_TS, SEED_BLOCKS=build_local_seed_blocks(modules))
        files[fe / "src/data/local-store.ts"] = render(
            LOCAL_STORE_TS, APP_SLUG=spec["app_slug"])
        files[fe / "src/api/local-service.ts"] = LOCAL_SERVICE_TS
    else:
        files[fe / "src/api/client.ts"] = CLIENT_TS

    view_imports = []
    routes = []
    for module in modules:
        key = module["key"]
        cls = class_name(key)
        view_imports.append(f"import {cls}View from './views/{key}'")
        routes.append(
            "          <Route path=\"" + f"/{key}" + "\" element={<" + cls + "View />} />"
        )
        view_body = render(
            REACT_VIEW_LOCAL if frontend_only else REACT_VIEW,
            KEY=key, NAME=module["name"], ENTITY=module["entity"],
            DESC=module_desc(module), CLASS=cls,
            FIELDS=json.dumps(module["fields"], ensure_ascii=False, indent=2),
            ACTIONS=json.dumps(module.get("actions") or [f"提交{module['name']}"],
                                ensure_ascii=False, indent=2),
            STATUSES=json.dumps(module.get("statuses") or ["待处理", "处理中", "已完成"],
                                ensure_ascii=False, indent=2),
            METRICS=json.dumps([{"label": label, "value": 0} for label in module_metrics(module)],
                               ensure_ascii=False, indent=2),
        )
        if frontend_only:
            # 主体识别会跳过 index.tsx 这种入口名，所以组件本身落到 <Key>View.tsx，
            # index.tsx 只做出口（标准的 React barrel 写法）。
            files[fe / f"src/views/{key}/index.tsx"] = (
                f"export {{ default }} from './{cls}View'\n")
            files[fe / f"src/views/{key}/{cls}View.tsx"] = view_body
        else:
            files[fe / f"src/views/{key}/index.tsx"] = view_body
    fallback_cards = json.dumps(
        [{"label": "业务模块", "value": 0}, {"label": "今日新增", "value": 0}], ensure_ascii=False)
    fallback_modules = json.dumps(
        [{"name": module["name"], "created": 0, "pending": 0, "abnormal": 0} for module in modules],
        ensure_ascii=False)
    files[fe / "src/views/Dashboard.tsx"] = (
        DASHBOARD_REACT_LOCAL if frontend_only
        else render(DASHBOARD_REACT, FALLBACK_CARDS=fallback_cards,
                    FALLBACK_MODULES=fallback_modules))
    files[fe / "src/App.tsx"] = render(
        APP_REACT, APP_NAME=spec["app_name"], APP_DESC=spec["app_desc"],
        NAV_ITEMS=build_nav_items(modules),
        REACT_VIEW_IMPORTS="\n".join(view_imports),
        REACT_ROUTES="\n".join(routes))

    for path, content in files.items():
        write(path, content)
        lines += line_count(content)
    return lines


def gen_backend_python(spec: dict, out: Path) -> int:
    modules = spec["modules"]
    be = out / "backend"
    lines = 0
    files: dict[Path, str] = {}
    files[be / "requirements.txt"] = REQUIREMENTS
    files[be / "run.sh"] = render(BACKEND_RUN_SH, BACKEND_PORT=spec["port_backend"])
    files[be / "Dockerfile"] = render(BACKEND_DOCKERFILE, BACKEND_PORT=spec["port_backend"])
    files[be / "app/__init__.py"] = ""
    files[be / "app/services/__init__.py"] = ""
    files[be / "app/main.py"] = render(
        BACKEND_MAIN, APP_NAME=spec["app_name"], BACKEND_PORT=spec["port_backend"])
    files[be / "app/routers/__init__.py"] = render(
        ROUTERS_INIT,
        IMPORTS="\n".join(
            f"from app.routers import {module['key']} as router_{module['key']}"
            for module in modules),
        MODULES=", ".join(f"router_{module['key']}" for module in modules),
    )
    files[be / "app/config.py"] = render(
        BACKEND_CONFIG, APP_NAME=spec["app_name"], BACKEND_PORT=spec["port_backend"],
        FRONTEND_PORT=spec["port_frontend"])
    files[be / "app/store.py"] = BACKEND_STORE

    schema_blocks = []
    seed_blocks = []
    for module in modules:
        cls = class_name(module["key"])
        schema_blocks.append(
            "\n\nclass " + cls + "Entry(BaseModel):\n"
            f'    """{module["entity"]}明细结构。"""\n\n'
            + "\n".join(
                f"    {('field_' + str(index))}: str | None = None  # {field}"
                for index, field in enumerate(module["fields"])
            )
        )
        rows = []
        statuses_for_seed = module_actions(module)["statuses"]
        for index in range(3):
            row = {"id": index + 1, "status": statuses_for_seed[index % len(statuses_for_seed)],
                   "pending": index < 2, "abnormal": index == 1}
            for field in module["fields"]:
                row[field] = sample_value(field, module["key"], module["name"], index)
            rows.append(row)
        seed_blocks.append(
            "    " + json.dumps(module["key"], ensure_ascii=False) + ": "
            + py_literal(rows, indent=4)
        )
    files[be / "app/schemas.py"] = render(BACKEND_SCHEMAS, SCHEMAS="".join(schema_blocks))
    files[be / "app/seed.py"] = render(BACKEND_SEED, SEED_TABLE=",\n".join(seed_blocks))

    for module in modules:
        key = module["key"]
        cls = class_name(key)
        flow = module_actions(module)
        statuses = flow["statuses"]
        actions = flow["actions"]
        files[be / f"app/routers/{key}.py"] = render(
            ROUTER_PY,
            KEY=key, NAME=module["name"], ENTITY=module["entity"], CLASS=cls,
            FIELDS=py_list(module["fields"]),
            STATUSES=py_list(statuses),
            FIELD0=module["fields"][0],
            ACTION_JOIN="、".join(actions),
            STATUS_JOIN="、".join(statuses),
        )
        files[be / f"app/services/{key}.py"] = render(
            SERVICE_PY,
            KEY=key, NAME=module["name"], ENTITY=module["entity"], CLASS=cls,
            REQUIRED_FIELDS=py_list(module["fields"][:3]),
            STATUSES=py_list(statuses),
            FIELD0=module["fields"][0],
            ACTION_RULES=json.dumps(flow["rules"], ensure_ascii=False),
            NEGATIVE_ACTIONS=py_list(flow["negative"]),
        )

    for path, content in files.items():
        write(path, content)
        lines += line_count(content)
    return lines


def gen_backend_java(spec: dict, out: Path) -> int:
    modules = spec["modules"]
    be = out / "backend"
    pkg = spec["app_slug"].replace("-", "")
    lines = 0
    files: dict[Path, str] = {}
    files[be / "pom.xml"] = render(JAVA_POM, APP_SLUG=spec["app_slug"], APP_NAME=spec["app_name"])
    files[be / "run.sh"] = render(JAVA_RUN_SH)
    files[be / "Dockerfile"] = render(BACKEND_DOCKERFILE_JAVA, BACKEND_PORT=spec["port_backend"])
    files[be / "src/main/resources/application.yml"] = render(
        JAVA_APP_YML, APP_SLUG=spec["app_slug"], BACKEND_PORT=spec["port_backend"])
    files[be / f"src/main/java/com/example/{pkg}/Application.java"] = render(
        JAVA_APPLICATION, PKG=pkg, APP_NAME=spec["app_name"], BACKEND_PORT=spec["port_backend"])
    files[be / f"src/main/java/com/example/{pkg}/controller/OverviewController.java"] = render(
        JAVA_OVERVIEW, PKG=pkg, APP_NAME=spec["app_name"])
    files[be / f"src/main/java/com/example/{pkg}/service/ModuleService.java"] = render(
        JAVA_MODULE_SERVICE, PKG=pkg)
    files[be / f"src/main/java/com/example/{pkg}/service/OverviewService.java"] = render(
        JAVA_OVERVIEW_SERVICE, PKG=pkg)

    for module in modules:
        key = module["key"]
        cls = class_name(key)
        flow = module_actions(module)
        statuses = flow["statuses"]
        actions = flow["actions"]
        action_rules = list(flow["rules"].items())
        files[be / f"src/main/java/com/example/{pkg}/controller/{cls}Controller.java"] = render(
            JAVA_CONTROLLER, PKG=pkg, KEY=key, NAME=module["name"], ENTITY=module["entity"],
            CLASS=cls, FIELD0=module["fields"][0], ACTION_JOIN="、".join(actions))
        files[be / f"src/main/java/com/example/{pkg}/service/{cls}Service.java"] = render(
            JAVA_SERVICE, PKG=pkg, KEY=key, NAME=module["name"], ENTITY=module["entity"],
            CLASS=cls,
            REQUIRED_FIELDS=", ".join(json.dumps(f, ensure_ascii=False)
                                      for f in module["fields"][:3]),
            STATUSES=", ".join(json.dumps(s, ensure_ascii=False) for s in statuses),
            ACTION_RULES=", ".join(
                f"{json.dumps(a, ensure_ascii=False)}, {json.dumps(t, ensure_ascii=False)}"
                for a, t in action_rules),
            NEGATIVE_ACTIONS=", ".join(json.dumps(a, ensure_ascii=False)
                                       for a in flow["negative"]) or '"__none__"',
        )

    for path, content in files.items():
        write(path, content)
        lines += line_count(content)
    return lines


def build_readme(spec: dict) -> str:
    modules = spec["modules"]
    table = "\n".join(
        f"| {module['name']} | `{module['key']}` | {module['entity']} | "
        f"{'、'.join(module['fields'][:3])} |"
        for module in modules
    )
    frontend_label = "Vue 3 + Vite + TypeScript" if spec["frontend"] == "vue" else "React + Vite + TypeScript"
    module_table = "| 模块 | 目录 | 业务对象 | 主要字段 |\n| --- | --- | --- | --- |\n" + table
    if spec["backend"] == "none":
        return render(
            README_LOCAL,
            APP_NAME=spec["app_name"], APP_DESC=spec["app_desc"],
            FRONTEND_LABEL=frontend_label,
            FRONTEND_PORT=spec["port_frontend"],
            STORAGE_KEY=f"{spec['app_slug']}:entries",
            MODULE_TABLE=module_table,
        )
    backend_label = "FastAPI（Python）" if spec["backend"] == "python" else "Spring Boot（Java 17）"
    backend_setup = "python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    backend_run = "./run.sh"
    return render(
        README,
        APP_NAME=spec["app_name"], APP_DESC=spec["app_desc"],
        FRONTEND_LABEL=frontend_label, BACKEND_LABEL=backend_label,
        FRONTEND_PORT=spec["port_frontend"], BACKEND_PORT=spec["port_backend"],
        BACKEND_SETUP=backend_setup, BACKEND_RUN=backend_run,
        MODULE_TABLE=module_table,
    )


def repo_kind(spec: dict) -> str:
    return "frontend-only" if spec["backend"] == "none" else "fullstack"


def scaffold(spec: dict, out: Path) -> dict:
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"目标目录非空，先清空或换一个目录：{out}")
    out.mkdir(parents=True, exist_ok=True)
    frontend_only = spec["backend"] == "none"
    lines = 0
    lines += gen_frontend_vue(spec, out) if spec["frontend"] == "vue" else gen_frontend_react(spec, out)
    if not frontend_only:
        lines += gen_backend_python(spec, out) if spec["backend"] == "python" else gen_backend_java(spec, out)

    root_files = {
        out / ".gitignore": GITIGNORE,
        out / "README.md": build_readme(spec),
        out / "docker-compose.yml": (
            render(COMPOSE_LOCAL, FRONTEND_PORT=spec["port_frontend"]) if frontend_only
            else render(COMPOSE, BACKEND_PORT=spec["port_backend"],
                        FRONTEND_PORT=spec["port_frontend"])),
        out / "Makefile": (
            MAKEFILE_LOCAL if frontend_only
            else render(MAKEFILE,
                        BACKEND_INSTALL="python3 -m venv .venv && .venv/bin/pip install -r requirements.txt",
                        BACKEND_RUN="./run.sh")),
        out / ".env.example": "APP_ENV=local\n",
    }
    for path, content in root_files.items():
        write(path, content)
        lines += line_count(content)

    run_sh = out / "backend" / "run.sh"
    if run_sh.exists():
        run_sh.chmod(0o755)
    return {"files": sum(1 for _ in out.rglob("*") if _.is_file()), "lines_written": lines}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成一个满足硬性要求的代码仓库（全栈或纯前端）")
    target = parser.add_mutually_exclusive_group(required=False)
    target.add_argument("--out", help="输出目录的完整路径（必须为空或不存在）")
    target.add_argument("--parent", help="父目录：按项目编号建 <父目录>/<前缀><5 位数>/<前缀><5 位数>，外层是任务容器、内层才是仓库根，例如 cc10001/cc10001")
    parser.add_argument("--spec", help=f"题库 spec json；默认用 examples/{DEFAULT_SPEC_NAME}")
    parser.add_argument("--check-spec", action="store_true",
                        help="只读 spec、校验格式并扫严禁题域，打印报告后退出，不生成任何目录")
    parser.add_argument("--frontend", choices=["vue", "react"], help="覆盖 spec 里的前端技术栈")
    parser.add_argument("--backend", choices=["python", "java", "none"],
                        help="覆盖 spec 里的后端技术栈；none = 只生成纯前端仓库")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX,
                        help=f"项目编号前缀，2~4 个字母，默认 {DEFAULT_PREFIX}（大小写按传入的原样用）")
    parser.add_argument("--number", type=int, default=None,
                        help="指定 5 位编号（10001~19999）；不传就从目录里已有的编号往后排")
    parser.add_argument("--owner", help="GitHub owner，用来查这个编号的仓库是否已存在；默认取 gh 当前账号")
    parser.add_argument("--no-github-check", action="store_true",
                        help="不查 GitHub（离线时用）；此时只按本地目录往后排编号")
    parser.add_argument("--git-init", action="store_true", help="顺手 git init 并做一次提交")
    args = parser.parse_args()

    spec_path = Path(args.spec).expanduser() if args.spec else SKILL_DIR / "examples" / DEFAULT_SPEC_NAME

    if args.check_spec:
        # 只自检：读 spec -> 校验格式 -> 扫严禁题域，不碰磁盘。
        try:
            spec = load_spec(spec_path, args.frontend, args.backend)
        except SystemExit as exc:
            print(json.dumps({
                "ok": False,
                "spec": str(spec_path),
                "reason": str(exc),
            }, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({
            "ok": True,
            "spec": str(spec_path),
            "app_name": spec["app_name"],
            "modules": len(spec["modules"]),
            "kind": repo_kind(spec),
            "banned_topic_hits": [],
            "gate_version": rules.GATE_VERSION,
        }, ensure_ascii=False, indent=2))
        return 0

    if not args.out and not args.parent:
        parser.error("需要 --out 或 --parent 之一（或改用 --check-spec 只自检 spec）")

    spec = load_spec(spec_path, args.frontend, args.backend)

    project: dict[str, object] = {}
    if args.parent:
        parent = Path(args.parent).expanduser().resolve()
        parent.mkdir(parents=True, exist_ok=True)
        check_github = not args.no_github_check
        owner = resolve_owner(args.owner) if check_github else args.owner
        project = pick_project_id(parent, args.prefix, explicit_number=args.number,
                                  owner=owner, check_github=check_github)
        # 多套一层：外层 <编号> 是任务容器，内层 <编号> 才是仓库根。
        # 即 <父目录>/cc10001/cc10001/{frontend,backend,...}
        container = parent / str(project["project_id"])
        out = container / str(project["project_id"])
        project["container"] = str(container)
        if not project.get("github_checked") and check_github:
            project["github_check_note"] = "没能查 GitHub（gh 未安装 / 未登录 / 未解析到 owner），只按本地目录排的编号"
    else:
        out = Path(args.out).expanduser().resolve()
        parsed = split_project_id(out.name)
        if parsed:
            project = {"project_id": out.name, "prefix": parsed[0], "number": parsed[1],
                       "picked_from": "--out 目录名", "github_checked": False, "skipped": []}

    result = scaffold(spec, out)

    if args.git_init:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=out, check=True)
        subprocess.run(["git", "add", "-A"], cwd=out, check=True)
        subprocess.run(["git", "-c", "user.email=repo@example.com", "-c", "user.name=repo",
                        "commit", "-q", "-m",
                        f"chore: 初始化{'纯前端' if spec['backend'] == 'none' else '全栈'}项目骨架"],
                       cwd=out, check=True)

    print(json.dumps({
        "ok": True,
        "out": str(out),
        "repo_root": str(out),
        "project": project,
        "app_name": spec["app_name"],
        "frontend": spec["frontend"],
        "backend": spec["backend"],
        "kind": repo_kind(spec),
        "modules": len(spec["modules"]),
        "files": result["files"],
        "lines_written": result["lines_written"],
        "next": f"python3 {SCRIPT_DIR / 'verify_repo.py'} --repo {out}",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
