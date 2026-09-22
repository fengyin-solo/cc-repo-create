---
name: cc-repo-create
description: "生成可直接出题、并且能直接交付的全栈代码仓库：前端 Vue 或 React + 后端 Java 或 Python，基础环境自洽（能编译、依赖声明齐、无写死路径）、在干净克隆里真装依赖真起服务真打开页面，前端 dev server 关掉自动打开页面，根目录带覆盖依赖/缓存/构建产物的 .gitignore，源码 >= 2000 行，按「主体 × 需求模式」算出的出题容量 >= 30 条，不含代码理解这类禁用题型，准备好后把仓库建成 GitHub 仓库并推送 main 分支（只允许快进、拒绝强推）。自带生成器、静态验收器、干净克隆启动探针与推送脚本（含 React/Java 变体与闸门自测），被单独拿到别的机器也能跑，不依赖任何其他 skill。"
---

# 全栈代码仓库生成（出题底座）

给一个题域，生成一个**能起、能看、能出题**的全栈仓库。这个仓库是下游出题的底座：
每个业务模块都同时有前端页面、后端接口和业务规则三处代码，主体数、行数、需求模式
覆盖度都按出题容量算好，不用生成完再回头补。

`<skill>` 指本 skill 所在目录。

## 什么时候用

- 用户要给某个题域「生成一个仓库 / 造一个项目 / 做一个全栈 demo」，并且这个仓库后面要出题；
- 用户拿到一批任务目录，缺可用底座，要先造几个仓库出来；
- 用户说「仓库太小 / 出不了 30 条题 / 页面打不开 / 没 .gitignore」这类问题时，用它造合规底座。

不做什么：不写题面、不建 A/B 分支、不跑 Claude、不验收模型产物——那是出题与验收链路的活。
远端只做一件事：把准备好的仓库建成 GitHub 仓库并推 `main` 分支（见第 5 步）。

## 七条硬性要求（全部机器复核，不许凭印象）

| 闸门 | 要求 |
|---|---|
| G1 结构 | 前端 Vue 或 React，后端 Java 或 Python，双端各有依赖清单与启动入口 |
| G2 启动 | 前端 dev server 关掉「自动打开页面」（Vite 写 `server.open = false`） |
| G3 忽略 | 根目录 `.gitignore` 覆盖依赖、缓存、构建产物、日志、环境文件、IDE、OS 文件 |
| G4 体量 | 源码 >= 2000 行（不含依赖目录、构建产物、锁文件） |
| G5 容量 | 出题容量 >= 30 条（主体数 × 每主体上限，需求模式数 × 每模式上限，再和单仓库上限取小） |
| G6 合规 | 不含禁用题型（`代码理解`），无密钥、无超大文件、无违规业务域 |
| G7 环境 | 基础环境自洽：Python 全量可编译、后端入口能 import、依赖都声明了、没有写死本机路径 |

外加一道 **G8 真启动**复核：后端真的起来并返回数据、前端页面真的有挂载点、
从装依赖到关服务全程不拉起浏览器、结束以后进程全停干净。**默认在干净克隆里跑**，
所以「本地残留让它跑起来」这种假通过不算数。

口径细节、容量公式推导、禁用项判据都在 [references/hard-rules.md](references/hard-rules.md)。

## 标准流程

### 1. 定 spec

复制一份 `examples/spec-park-pass.json`，改题域与业务模块。**模块数 >= 16**，
每个模块 6~8 个具体业务字段（别写「数据 / 状态 / 用户」这类泛词，主体识别会过滤掉），
动作至少 2~3 个且方向不同（正向推进 + 打回/作废）。

字段说明与挑题域的注意事项见 [references/spec-format.md](references/spec-format.md)。

### 2. 生成

```bash
# 推荐：给一个父目录，按项目编号自动命名（cc10001 / cc10002 / …）
python3 <skill>/scripts/scaffold_repo.py --parent /path/to/父目录 --spec /path/to/spec.json --git-init
# 换前缀：--prefix YEL → YEL10001、YEL10002…
# 换技术栈：--frontend vue|react  --backend python|java
# 显式给路径（不做编号分配）：--out /path/to/repo
```

输出里带 `modules` / `files` / `lines_written`，以及下一步该跑的验收命令。
`--git-init` 会顺手 `git init` 并做一次提交，便于后面用 `git check-ignore` 实测忽略规则。
编号规则见下面「项目编号」一节。

### 3. 静态验收

```bash
python3 <skill>/scripts/verify_repo.py --repo /path/to/repo          # 人读版
python3 <skill>/scripts/verify_repo.py --repo /path/to/repo --json   # 留证据
```

非 0 退出码就是没过，输出会点名是哪道闸门、哪个文件。改完重跑，不要绕过。

### 4. 真启动验收（在干净克隆里跑）

```bash
python3 <skill>/scripts/probe_startup.py --repo /path/to/repo --clean-clone --build
```

`--clean-clone` 会先把仓库 clone 到一个干净临时目录，再在克隆里跑完整探针——
依赖目录不会跟过来（被 `.gitignore` 挡住了），必须现场装一遍才能起来，
这一步同时证明了「仓库里没有未提交的必需文件」「依赖目录没被提交」。

探针会：建 venv / 装 requirements 或 `npm install`；起后端并轮询 `/api/health`、
`/api/overview` 与一个业务接口；起前端 dev server 并检查首页 HTML 有挂载点、
入口脚本能加载、**从前端端口走 `/api/health` 验证代理链路**；`--build` 再跑一次
`npm run build`（含 typecheck）；最后整组杀进程。

每步都是 `ok: true/false`，`cleaned_up` 表示进程是否停干净。日志默认落在
`<repo 的父目录>/.probe-logs/<repo 名>/`。

Java 后端在没装 JDK 17 + Maven 的机器上会明确报「无法真启动」，
**这一条按未验证处理，不许写成已通过**。

### 5. 推到 GitHub（main 分支）

仓库完全准备好以后才推。默认 dry-run，只打印计划：

```bash
python3 <skill>/scripts/publish_github.py --repo /path/to/repo
python3 <skill>/scripts/publish_github.py --repo /path/to/repo \
  --owner <账号或组织> --name <仓库名> --visibility public \
  --execute --confirm PUBLISH
```

它会：校验仓库是 git 仓库、当前分支是 `main`、工作区干净、静态验收通过；解析 owner
（默认取 `gh` 当前登录账号）；仓库不存在就 `gh repo create` 建一个；把 `origin` 设成
**不带 token** 的 `https://github.com/<owner>/<name>.git`；**核对可见性并确保是 public**；
推送 `main:main`；最后回读远端 `main` 的 SHA 和本地 HEAD 比对。

**生成的仓库必须是 public**（`--visibility` 默认就是 `public`）：下游容器要能匿名 clone，
private 会直接把出题链路卡住。脚本不靠「建的时候传了 public」就算数，而是推送前后各读一次
真实可见性：发现远端还是 private，就用
`gh repo edit <owner>/<name> --visibility public --accept-visibility-change-consequences`
改过来，并把 `visibility_before` / `visibility_after` / `visibility_fixed` 写进报告；
改完仍然不是 public 就直接报错，不当作成功。

安全边界（都实测过）：

- 工作区有未提交改动 → 拒推；
- 远端 `main` 上有本地没有的提交（分叉）→ 拒推，**脚本不做强推**；
- 远端已是最新 → 报 `up-to-date`，不重复推；
- 生成的仓库可见性不是 public → 自动改成 public 并在报告里留前后值；改不动就报错；
- token 只以一次性 `git -c http.extraHeader=...` 传参，不写进 `.git/config`，也不打印；
- 远端不是 GitHub（例如 GitLab 或本地裸仓库）→ 用 `--remote-url` 直接推，跳过 `gh` 建仓。

### 6. 交付前自查

- `verify_repo.py` 退出码 0；
- `probe_startup.py --clean-clone --build` 的 `ok` 为 `true`、`cleaned_up` 为 `true`，
  且克隆里那条「前端生产构建通过」也是 `true`；
- 依赖目录已被 `.gitignore` 命中，且没进 `git status --porcelain`；
- 推完之后 `publish_github.py` 报 `ok: true`，远端 `main` 的 SHA 等于本地 HEAD。
- 生成出来的仓库是 **public**（报告里 `visibility_after` 是 `public`）。

## 项目编号（默认 cc10001 起，前缀可换）

用 `--parent` 生成时，目录名与仓库名都取**项目编号**：

```text
<前缀 2~4 个字母><5 位数>      5 位数首位固定是 1（10001~19999）
cc10001、cc10002、YEL10003 …
```

- **前缀默认 `cc`**，`--prefix YEL` 就得到 `YEL10001`；**大小写按传入的原样用**
  （想要大写就传 `CC`，想要小写就传 `cc`）。
- **编号接着当前目录已有的往后排**：扫描父目录里所有 `<字母><5 位数>` 目录，取最大编号 + 1；
  换前缀不会重置序号（`cc10007` 之后用 `--prefix YEL` 会得到 `YEL10008`）。
- **GitHub 上已存在的编号自动跳过**：逐个候选编号查 `gh repo view <owner>/<编号>`，
  命中就往后挪一个，直到找到没被占的。owner 默认取 `gh` 当前登录账号，可用 `--owner` 指定。
  跳过的编号会写进输出的 `project.skipped`。
- `gh` 没装 / 没登录 / 解析不到 owner 时**不卡住**：回退成本地序号继续，并把
  `project.github_checked` 标成 `false`、附一句 `github_check_note`。离线可用 `--no-github-check` 显式跳过。
- `--number 10042` 可以指定编号，但仍然会校验：本地目录已存在或 GitHub 上被占用就直接报错，
  不自动往后挪（显式指定就是要你确认过）。
- 编号封顶 19999；到顶了报错让你换前缀或清理，不会溢出成 6 位数。

生成后 `publish_github.py` 的默认仓库名就是目录名，也就是这个编号，两边不会对不上。

## 出题容量：30 条这一条怎么成立（必读）

容量 = `min(单仓库上限 35, 主体数 × 每主体上限 2, 可用需求模式数 × 每模式上限)`。
可用需求模式共 **12 类**（需求模式词典 13 类，去掉禁用的 `代码理解`）。

**模式席位是硬上限，跟仓库做多大无关**：

| 每模式上限 | 模式席位 | 单仓库最多能出 |
|---|---|---|
| 2（旧默认） | 24 | 24 条，**任何仓库都到不了 30** |
| 3（现行口径） | 36 | 主体数 >= 16 时 >= 32 条 |

要让一个仓库真的出满 30 条，两件事缺一不可：

1. **仓库侧**：主体数 >= 16（默认 spec 实测 29 个主体，容量 35，留足余量）；
2. **出题侧**：每模式上限取 3。**这一条已经落地**：GSB 建仓脚本把每模式上限的默认值
   改成 3，写表命令 `batch_prompt_workbook.py update` 与独立复检 `check_repo_theme.py`
   都支持透传 `--max-per-mode`，GSB 批次三条命令统一带 `--max-per-mode 3`。

`verify_repo.py` 默认也按 `--per-mode 3` 验收，和出题侧同尺。如果哪一批要收回旧口径，
建仓、写表、复检三处的上限要**一起**改成 2，否则会出现「建仓按 3 排、写表在第 25 条开始拦」
互相打架；这时单仓库上限就是 24 条，不要按 30 条承诺。

## 禁用题型

任务类型里的 `代码理解` 是禁用项。仓库侧对应的判据是：前端页面组件 >= 8 个、
后端接口文件 >= 8 个，且业务代码带真实中文业务文案——只有文档、只有算法片段、
只有一个入口文件、或者通篇泛词（主体识别派不出 3 条非泛用中文串）的仓库都会被拦下。

## 主体是怎么派生的（决定了文件必须怎么写）

- 只扫这些路径：`frontend*/src/**`、`backend/app/**`、`**/src/main/java/**`、
  `**/src/**/*.cpp`、`frontend*/**/*.css` 等，完整列表在 `scripts/repo_rules.py`
  的 `REPO_SOURCE_PATTERNS`。**业务代码必须放在这些路径下**，放到别处的主体数是 0。
- 一个文件 = 一个主体；`index` / `main` / `page` / `list` / `home` 这类入口文件
  取父目录名（`views/visitor/index.vue` → `visitor`）。
- 文件里至少要有 3 条非泛用中文串，否则这个文件不算主体。

## 规则同步（快照可能过期）

`scripts/repo_rules.py` 是出题闸门口径的冻结快照（抄写日期 2026-09-22）。换了新版的
出题流水线，先比对再生成：

```bash
python3 <skill>/scripts/verify_repo.py --rules-drift <任一份外部出题闸门脚本的路径>
```

这个参数是可选的：只在目标机器上已经有出题闸门脚本（例如 `check_repo_theme.py`）时才用，
脚本会逐个比对 `REPO_SOURCE_PATTERNS` / `DEMAND_MODE_LEXICON` 等常量。`ok: false` 时按输出
点名的那几个常量更新 `repo_rules.py`、更新 `GATE_VERSION`、重跑自测。
没有这份脚本时跳过这一步，本 skill 不依赖它也能独立生成与验收。

## 自测

```bash
python3 -m unittest discover -s <skill>/tests -v
```

35 条用例：生成出来的仓库要过全部闸门；把 `open: false` 改成 `true`、删掉 `.gitignore`、
塞一个硬编码密钥、写死一个本机绝对路径、漏声明一个依赖、把 `true` 当 Python 变量用、
把模块砍到 3 个、把主体砍到 2 个，闸门都必须拦下；依赖目录落地后必须被 `git check-ignore`
命中；React + Java 变体也要过；推送链路要覆盖「dry-run 不碰远端」「首次推 main」
「重复推是 up-to-date」「新提交快进」「远端分叉拒推」「脏工作区拒推」「不是 git 仓库报错」。
编号链路要覆盖「空目录从 10001 起」「按已有编号往后排」「换前缀不重置序号」「大小写保持」
「GitHub 占用时往后跳」「查不了 GitHub 时回退本地序号」「显式编号冲突就报错」「编号封顶」。
可见性链路要覆盖「已经是 public 就不动」「private 会被改成 public」「dry-run 只计划不改」
「改完还是 private 要报错」「读不到可见性不阻塞」。

改了 `scaffold_repo.py` / `verify_repo.py` / `repo_rules.py` / `publish_github.py`
先跑它，再动真实仓库。
