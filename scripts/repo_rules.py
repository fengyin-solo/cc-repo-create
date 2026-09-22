"""代码仓库生成与验收的冻结规则快照（本 skill 自带，不依赖任何其他 skill）。

这里保存的是「一个仓库最多能出多少条题」这条容量公式所依赖的全部口径：
源码扫描模式、主体命名规则、需求模式词典、忽略目录。它们是从实际生效的出题闸门里抄出来的
快照，抄写日期 2026-09-22，见 GATE_VERSION。

为什么要抄一份而不是直接 import：本 skill 会被单独拿到别的机器 / 别的流程里用，不能假设
出题流水线的脚本一定在。抄一份的代价是可能过期，所以：

  1. 拿到新版出题流水线后，用 verify_repo.py --rules-drift <那台机器上的 check_repo_theme.py>
     比对；不一致时按 references/hard-rules.md 的「规则同步」一节更新本文件。
  2. 本文件只做三件事：派生主体、算容量、判忽略。不参与写题，也不改任何仓库。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

GATE_VERSION = "cc-repo-create-2026-09-22"

# ---------------------------------------------------------------------------
# 源码扫描模式：只有命中这些模式的文件才会被当成「可独立出题的主体」。
# 生成的仓库必须把业务代码放在这些路径下，否则主体派生落空、容量被算成 0。
# ---------------------------------------------------------------------------
REPO_SOURCE_PATTERNS: tuple[str, ...] = (
    "frontend/src/**/*.tsx",
    "frontend/src/**/*.ts",
    "frontend/src/**/*.vue",
    "frontend/src/**/*.jsx",
    "backend/app/**/*.py",
    "src/**/*.ts",
    "src/**/*.tsx",
    "app/**/*.py",
    "**/src/main/java/**/*.java",
    "backend/**/*.java",
    "frontend*/**/*.html",
    "**/static/**/*.html",
    "frontend*/src/**/*.vue",
    "frontend*/src/**/*.ts",
    "frontend*/src/**/*.tsx",
    "web*/src/**/*.vue",
    "web*/src/**/*.ts",
    "web*/src/**/*.tsx",
    "**/src/**/*.cpp",
    "**/src/**/*.cc",
    "**/include/**/*.h",
    "**/include/**/*.hpp",
    "frontend*/**/*.py",
    "web*/**/*.py",
    "frontend*/js/**/*.js",
    "web*/js/**/*.js",
    "frontend*/src/**/*.js",
    "web*/src/**/*.js",
    "frontend*/**/*.css",
    "web*/**/*.css",
    "app*/**/*.css",
    "frontend*/Dockerfile",
    "web*/Dockerfile",
    "app*/Dockerfile",
    "frontend*/nginx.conf",
    "web*/nginx.conf",
    "app*/nginx.conf",
)

# 用文件名看不出模块的入口文件：一律改用父目录名当主体。
PARENT_NAMED_STEMS = {"index", "main", "page", "list", "home"}
# 这些目录名太泛，不能当主体（views/visitor/index.vue -> visitor，而不是 views）。
GENERIC_DIR_NAMES = {
    "src", "app", "pages", "views", "components", "common", "layout", "api",
    "stores", "store", "styles", "types", "router", "utils", "composables",
    "hooks", "assets", "modules", "features",
}
# 主体识别时排除的构建产物 / 依赖目录。
IGNORED_SOURCE_DIR_PARTS = {
    "node_modules", "dist", "build", "out", ".git", "vendor", ".next", "coverage",
    "__pycache__", ".venv", "venv", "target", ".gradle", ".tox",
}
# 上面的集合必须与出题链路逐字一致（--rules-drift 会比对），所以只加验收侧自己要跳过的目录。
SCAN_IGNORED_DIR_PARTS = IGNORED_SOURCE_DIR_PARTS | {
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode", ".cache",
}
# 每个主体文件至少要含这么多条中文串，否则不算一个可出题主体。
MIN_TOKENS_PER_SUBJECT = 3
# 仓库里实际源码文件多、派生主体却 <=2 个时报警（扫描口径可能没覆盖目录布局）。
DERIVATION_HEALTH_MIN_SOURCE_FILES = 8
DERIVATION_HEALTH_MAX_SUBJECTS = 2

CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,8}")

# 主体识别时忽略的中文串：这些词在每个组件里都会出现，拿来定主体会把所有文件混成一堆。
GENERIC_TOKENS = {
    "用户", "新增", "支持", "显示", "状态", "数据", "界面", "功能", "面板", "列表",
    "提示", "结果", "内容", "信息", "时间", "时刻", "切换", "保存", "说明", "记录",
    "操作", "查看", "原有", "保持", "默认", "边界", "失败", "重试", "异常", "空态",
    "现在", "希望", "需要", "可以", "不能", "如果", "以及", "并且", "同时", "之后",
}

# ---------------------------------------------------------------------------
# 需求模式：平台查重规则 C 判的是「主体 + 需求模式」这两根轴，不是措辞。
# 一个仓库能出的题数上限由这张表的条数决定，它是容量公式里的第二个乘数。
# ---------------------------------------------------------------------------
DEMAND_MODE_LEXICON: dict[str, tuple[str, ...]] = {
    "新增展示视图": ("视图", "展示", "列出", "呈现", "概览", "看板", "排列", "显示出来"),
    "规则与阈值": ("阈值", "口径", "判定", "规则", "上限", "范围", "条件", "标准", "不允许保存"),
    "状态流转": ("流转", "状态", "生命周期", "归档", "恢复", "回到", "切换", "变更"),
    "权限与归属": ("权限", "越权", "归属", "只读", "受控", "只能查看", "不能改动", "共享"),
    "批量操作": ("批量", "多条", "多选", "逐条", "一次提交", "逐台", "整组"),
    "导入导出": ("导入", "导出", "文件", "下载", "上传", "打包", "书签"),
    "列表定位与筛选": ("筛选", "排序", "翻页", "分页", "定位", "检索", "搜索", "查询", "过滤"),
    "持久化与一致性": ("刷新", "返回", "重新进入", "持久", "一致", "对得上", "保留原", "同步"),
    "边界与空态": ("空态", "暂无", "没有", "失败", "重试", "中断", "说明原因", "异常"),
    "缺陷处置": ("报错", "不生效", "残留", "点不动", "显示成", "对不上", "错位", "丢失", "重复显示"),
    "代码重构收拢": ("收拢", "各写了一遍", "共用", "抽取", "写成一份", "统一写法"),
    "工程化与流程": ("构建", "上线", "流水线", "依赖", "环境", "数据库", "示例数据", "部署", "本地开发"),
    "代码理解": ("读懂", "理清", "梳理", "分步说明", "流程图", "链路", "经过哪些环节"),
}

# 禁用任务类型：本 skill 生成的仓库必须能撑起这些以外的题型。
FORBIDDEN_TASK_TYPES = ("代码理解",)
# 出题时允许使用的需求模式 = 全部模式减去禁用类型那一格。
USABLE_MODES = tuple(m for m in DEMAND_MODE_LEXICON if m not in FORBIDDEN_TASK_TYPES)

# ---------------------------------------------------------------------------
# 容量口径
# ---------------------------------------------------------------------------
# 单仓库上限：平台是在同一个仓库里两两比对，铺得越多最近邻越近。
MAX_PER_REPO = 35
# 同一个主体最多出几条（放开到 2 条时两条必须跨任务家族）。
MAX_PER_SUBJECT = 2
# 同一个需求模式最多出几条。本 skill 按 3 计算容量：12 类可用模式 x 2 = 24 条到不了 30，
# x 3 = 36 条才够。GSB 出题链路现在也取 3（建仓 / 写表 / 复检三处统一）。
# 注意 check_repo_theme.py 里那个 DEFAULT_MAX_PER_MODE 仍是 2，本 skill 不复用它。
MAX_PER_MODE = 3
# 本 skill 的容量下限：一个仓库最少要能出 30 条。
MIN_CAPACITY = 30


def _module_name(path: Path) -> str:
    """主体名：普通文件用文件名，index/main 这类入口文件用父目录名。"""
    stem = path.stem
    if stem in PARENT_NAMED_STEMS:
        parent = path.parent.name
        if parent and parent not in GENERIC_DIR_NAMES:
            return parent
    return stem


def _iter_source_files(root: Path, patterns: Iterable[str]) -> Iterable[Path]:
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if path.name.endswith(".d.ts"):
                continue
            if path.name in {"main.ts", "main.tsx", "index.ts", "index.tsx"}:
                continue
            if any(part in IGNORED_SOURCE_DIR_PARTS for part in path.parts):
                continue
            if path.is_file():
                yield path


def derive_subjects(repo: Path | str, *, min_tokens: int = MIN_TOKENS_PER_SUBJECT) -> dict[str, set[str]]:
    """从仓库源码派生主体词典：一个文件 = 一个主体，关键词取文件里的中文串。

    与出题链路的派生口径一致：文件必须含 >= min_tokens 条非泛用中文串才算一个主体；
    长词再切成二字片段一起入词典，这样题面里的短词也能命中对应主体。
    """
    modules: dict[str, set[str]] = {}
    root = Path(repo)
    if not root.is_dir():
        return modules
    for path in _iter_source_files(root, REPO_SOURCE_PATTERNS):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        tokens = {t for t in CJK_RUN_RE.findall(text) if t not in GENERIC_TOKENS}
        fragments: set[str] = set()
        for token in list(tokens):
            for index in range(len(token) - 1):
                piece = token[index:index + 2]
                if piece not in GENERIC_TOKENS:
                    fragments.add(piece)
        tokens |= fragments
        if len(tokens) >= min_tokens:
            modules.setdefault(_module_name(path), set()).update(tokens)
    return modules


def compute_capacity(
    subjects: dict[str, set[str]] | Iterable[str],
    *,
    max_per_repo: int = MAX_PER_REPO,
    max_per_subject: int = MAX_PER_SUBJECT,
    max_per_mode: int = MAX_PER_MODE,
    usable_modes: int | None = None,
) -> dict[str, object]:
    """容量 = min(单仓库上限, 主体数 x 每主体上限, 可用需求模式数 x 每模式上限)。"""
    keys = sorted(subjects.keys()) if isinstance(subjects, dict) else sorted(set(subjects))
    if usable_modes is None:
        usable_modes = len(USABLE_MODES)
    subject_slots = len(keys) * max_per_subject
    mode_slots = usable_modes * max_per_mode
    capacity = min(max_per_repo, subject_slots, mode_slots)
    if mode_slots == capacity:
        binding = "mode"
    elif subject_slots == capacity:
        binding = "subject"
    else:
        binding = "repo"
    return {
        "capacity": capacity,
        "binding_limit": binding,
        "subject_count": len(keys),
        "subjects": keys,
        "subject_slots": subject_slots,
        "mode_slots": mode_slots,
        "usable_modes": usable_modes,
        "max_per_repo": max_per_repo,
        "max_per_subject": max_per_subject,
        "max_per_mode": max_per_mode,
        "rule": "容量 = min(单仓库上限, 主体数 x 每主体上限, 可用需求模式数 x 每模式上限)",
    }


def derivation_health(repo: Path | str, derived: dict[str, set[str]] | None = None) -> dict[str, object]:
    """源码文件多、派生主体却很少时报警：说明扫描口径可能漏掉了整个目录。"""
    root = Path(repo)
    if not root.is_dir():
        return {}
    wide_suffixes = {
        ".vue", ".ts", ".tsx", ".jsx", ".js", ".java", ".py", ".go", ".rb", ".php",
        ".kt", ".cpp", ".cc", ".h", ".hpp", ".css", ".html",
    }
    source_files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in wide_suffixes:
            continue
        if path.name.endswith(".d.ts"):
            continue
        if any(part in IGNORED_SOURCE_DIR_PARTS for part in path.parts):
            continue
        source_files.append(path)
    matched = list(_iter_source_files(root, REPO_SOURCE_PATTERNS))
    modules = derived if derived is not None else derive_subjects(root)
    warning = ""
    if (len(source_files) >= DERIVATION_HEALTH_MIN_SOURCE_FILES
            and len(modules) <= DERIVATION_HEALTH_MAX_SUBJECTS):
        warning = (
            f"主体派生可疑：仓库里实际有 {len(source_files)} 个源码文件（扫描模式只命中 "
            f"{len(matched)} 个），却只派生出 {len(modules)} 个主体。容量由主体数决定，"
            "先把业务代码放回扫描模式覆盖的路径（frontend*/src、backend/app 等）再建仓。"
        )
    return {
        "source_files": len(source_files),
        "matched_files": len(matched),
        "subjects": len(modules),
        "subject_names": sorted(modules)[:40],
        "warning": warning,
    }


# ---------------------------------------------------------------------------
# .gitignore 与「自动打开页面」的必检项
# ---------------------------------------------------------------------------
# 每一项给出若干等价写法，命中任意一种就算该项已忽略。
REQUIRED_GITIGNORE_RULES: dict[str, tuple[str, ...]] = {
    "前端依赖目录": ("node_modules/", "node_modules"),
    "前端构建产物": ("dist/", "dist", "build/", "build"),
    "后端缓存目录": ("__pycache__/", "__pycache__", "*.py[cod]", "*.pyc"),
    "Python 虚拟环境": (".venv/", ".venv", "venv/", "env/", "virtualenv"),
    "Java 构建产物": ("target/", "target", "*.class", ".gradle/", ".gradle"),
    "包管理器缓存": (".npm", ".yarn", ".pnpm-store", ".cache/", ".cache", ".parcel-cache"),
    "构建信息缓存": (".vite", ".tsbuildinfo", ".eslintcache", ".sass-cache"),
    "日志": ("*.log", "logs/"),
    "环境变量文件": (".env.local", ".env.*.local", ".env", "*.env"),
    "测试覆盖率": ("coverage/", "coverage", ".nyc_output"),
    "IDE 配置": (".idea/", ".idea", ".vscode/", ".vscode"),
    "操作系统文件": (".DS_Store", "Thumbs.db"),
}

# 这些目录一旦出现就必须被 .gitignore 覆盖，复制依赖时不许带进提交。
DEPENDENCY_DIR_NAMES = (
    "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target",
    ".gradle", ".pytest_cache", ".mypy_cache", ".vite", "coverage",
)

# 命中即判「会自己打开页面」。
AUTO_OPEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"open\s*:\s*true", "dev server 显式打开了自动打开页面（open: true）"),
    (r"--open\b", "启动脚本里带了 --open，起服务时会自动打开浏览器"),
    (r"BROWSER\s*=\s*(?!none)\w+", "启动脚本的 BROWSER 变量不是 none，会自动打开浏览器"),
    (r"openBrowser\s*:\s*true", "devServer.openBrowser 为 true"),
)


def iter_dependency_dirs(repo: Path | str) -> list[Path]:
    """列出仓库里已经出现的依赖 / 构建目录（复制依赖与判忽略都用它）。"""
    root = Path(repo)
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_dir() or path.name not in DEPENDENCY_DIR_NAMES:
            continue
        if ".git" in path.parts:
            continue
        found.append(path)
    return found
