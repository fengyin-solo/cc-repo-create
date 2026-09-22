# 硬性要求与容量口径

这个 skill 生成的每个仓库都要过六道闸门，全部由 `scripts/verify_repo.py` 机器复核，
再加一道 `scripts/probe_startup.py` 的真启动复核。任何一道不过就不算生成完成。

| 闸门 | 要求 | 由谁判 |
|---|---|---|
| G1 结构 | 前端 Vue 或 React、后端 Java 或 Python，双端各有依赖清单与启动入口 | `verify_repo.py` |
| G2 启动 | 前端 dev server 关掉「自动打开页面」 | `verify_repo.py` |
| G3 忽略 | 根目录 `.gitignore` 覆盖依赖 / 缓存 / 构建产物 / 日志 / 环境文件 | `verify_repo.py` |
| G4 体量 | 源码行数 >= 2000 行（不含依赖目录、构建产物、锁文件） | `verify_repo.py` |
| G5 容量 | 出题容量 >= 30 条 | `verify_repo.py` |
| G6 合规 | 不是禁用题型仓库，无密钥、无超大文件、无违规业务域 | `verify_repo.py` |
| G7 环境 | 基础环境自洽：Python 全量可编译、后端入口能 import、依赖都声明、没有写死本机路径 | `verify_repo.py` |
| G8 真启动 | 干净克隆里后端起得来、接口有数据、前端页面有挂载点、前端代理到后端通、生产构建通过 | `probe_startup.py` |

## G7 基础环境自洽（专治「本地能跑、换台机器就炸」）

`verify_repo.py` 的 `check_env_preconditions` 逐项验：

| 检查 | 为什么 |
|---|---|
| 所有 `.py` 都要能 `compile` | 语法错误必须在交付前发现 |
| 不许把 `true` / `false` / `null` 当 Python 变量 | 语法合法、一 import 就 `NameError`（真踩过：种子数据用 JSON 序列化写进 `.py`） |
| 真 import 一次后端入口（`app.main`） | 编译抓不到「少依赖」「写错模块名」；缺的包若已在 requirements 里声明，就记 `deferred` 交给启动探针 |
| 后端 import 的三方包必须写进 `requirements.txt` | 换台机器少装一个包就起不来 |
| 前端 import 的三方包必须写进 `package.json` | 同上 |
| 前端要有 `dev` 与 `build` 两个脚本 | 起不来、也验不了 |
| 源码里不许出现 `/Users/...`、`/home/...`、`/private/tmp/...` 这类本机绝对路径 | 写死路径换台机器必炸 |

## G8 真启动怎么算通过

```bash
python3 scripts/probe_startup.py --repo <repo> --clean-clone --build
```

`--clean-clone` 不是可选装饰：它先把仓库 `git clone` 到临时目录再跑，因此同时证明了三件事——

1. 依赖目录**没有**被提交进仓库（`node_modules` / `.venv` / `dist` / `build` 都不在克隆里）；
2. 仓库里没有「未提交但必需」的文件（克隆出来的工作区是干净的）；
3. 现场装依赖就能起来，不靠任何本地残留。

通过的判据：后端 `/api/health`、`/api/overview` 与一个业务接口都 200；前端首页含挂载点、
入口脚本可加载；从前端端口走 `/api/health` 返回 200（代理链路通）；`--build` 时
`npm run build` 退出码 0；结束时 `cleaned_up: true`（进程全停）。

## 推送到 GitHub（main 分支）

```bash
python3 scripts/publish_github.py --repo <repo>                     # dry-run
python3 scripts/publish_github.py --repo <repo> --execute --confirm PUBLISH
```

先决条件与拒绝项（都实测过）：必须是 git 仓库、当前分支是 `main`、工作区干净、
静态验收通过；**远端 `main` 上有本地没有的提交时拒推，脚本不做强推**。
分支已是最新时报 `up-to-date`，不重复推。推完回读远端 `main` 的 SHA 与本地 HEAD 比对。

**生成的仓库必须是 public**（`--visibility` 默认 `public`）：下游容器要能匿名 clone，
private 会卡住整条出题链路。脚本推送前后各读一次真实可见性，不是 private 就改成 public，
并把 `visibility_before` / `visibility_after` / `visibility_fixed` 写进报告；改不动就报错。
这是「生成的业务仓库」的口径——skill 自己的代码仓不受这条约束，按需要单独设。

凭据：默认走 `gh`（`gh auth token`），也认 `GH_TOKEN` / `GITHUB_TOKEN`。
token 只以一次性 `git -c http.extraHeader=...` 传参，**不写进 `.git/config`、不打印**；
`origin` 始终指向不带 token 的 `https://github.com/<owner>/<name>.git`。
远端不是 GitHub 时用 `--remote-url` 直接推。

## 禁用题型

任务类型一共六种：`代码生成` `缺陷修复` `功能迭代` `代码重构` `工程化` `代码理解`。
**`代码理解` 是禁用项**，出题侧不会出这一类，所以仓库也不能长成只能讲代码怎么走的样子。

机器判据（`check_forbidden`）：

- 前端页面组件少于 8 个、或后端接口文件少于 8 个 → 判「可独立出题的页面/接口太少」。
  只有文档、只有算法片段、只有一个入口文件的仓库都会被这一条拦下。
- 命中密钥特征（私钥、`AKIA...`、`ghp_...`、`sk-...`、硬编码口令）→ 拦下。
- 命中违规业务域（赌博、色情、毒品、枪支、金融黑灰产、攻击工具）→ 拦下。
- 单个文件超过 5MB → 拦下（产物快照会被判 too large）。

## 容量怎么算出来的

平台查重判的是**「主体 + 需求模式」**这两根轴：同一主体出多了、或同一需求模式出多了，
都会被判废。所以一个仓库能出多少条不是「想写多少写多少」，而是座位数：

```text
容量 = min(单仓库上限, 主体数 × 每主体上限, 可用需求模式数 × 每模式上限)
```

默认参数：

| 参数 | 值 | 说明 |
|---|---|---|
| 单仓库上限 | 35 | 同一个仓库里两两比对，铺得越多最近邻越近 |
| 每主体上限 | 2 | 同一个主体的第二条必须跨任务家族（新增能力类 vs 缺陷修复） |
| 每模式上限 | 3 | 全局默认值（`check_repo_theme.DEFAULT_MAX_PER_MODE`，2026-09-22 由 2 改成 3） |
| 可用需求模式 | 12 | 需求模式词典共 13 类，去掉禁用的 `代码理解` |

所以：`每模式上限 3` 时，模式席位 = 12 × 3 = 36；只要主体数 >= 16，
容量 = min(35, 主体数 × 2, 36) >= 32，稳稳过 30。默认 spec 实测 29 个主体，容量 35。

### 30 条这一条怎么成立

容量公式里的模式席位是硬上限：`12 类可用模式 × 每模式上限`。

- 每模式上限 = 2 时，模式席位只有 24，**任何仓库都到不了 30 条**，跟仓库做多大无关；
- 每模式上限 = 3 时，模式席位 36，此时容量由主体数决定，主体 16 个就够 32 条。

所以「一个仓库最少能出 30 条」这条要求有两个必要条件，缺一不可：

1. 仓库侧：主体数 >= 16（本 skill 默认 29 个，留足余量）；
2. 出题侧：每模式上限取 3。**已落地**——2026-09-22 把
   `check_repo_theme.DEFAULT_MAX_PER_MODE` 从 2 改成 3，所以建仓容量、写表闸门、
   独立复检、通用批量建仓**不传就是 3**；`batch_prompt_workbook.py update`、
   `check_repo_theme.py`、`create_batch_tasks_ab.py` 也都支持 `--max-per-mode` 显式覆盖，
   GSB 批次三条命令再显式带一次 `--max-per-mode 3` 把它钉死。

`verify_repo.py` 默认也按 `--per-mode 3` 验收，与出题侧同尺；要按旧口径复核加 `--per-mode 2`，
此时 `G5` 必然不过，这是符合预期的，不是仓库做错了。

想按更严的口径复核某一批，显式传 `--max-per-mode 2` 即可（一个仓库封顶 24 条）；
但**建仓、写表、复检三处要一起传**，只改一处会出现「建仓按 3 排出 35 个目录、
写表按 2 在第 25 条开始拒写」这种互相打架的批次。

放宽到 3 的代价是有界的：判废对的召回是靠**对级特征**（同主体 / 同需求模式交集）算的，
跟这个上限无关——把历史上那两批（cc-6600009 / cc-6600011）按每模式 3 重跑，
仍然被判不通过，对级召回一点没掉；变动的只是「一批里同类模式最多几条」。

### 主体是什么

主体 = 仓库里一个可以被单独改的代码位置，派生规则是冻结的（见 `scripts/repo_rules.py`）：

- 只扫这些路径下的文件：`frontend*/src/**`、`backend/app/**`、`**/src/main/java/**`、
  `**/src/**/*.cpp`、`frontend*/**/*.css` 等（完整列表见 `REPO_SOURCE_PATTERNS`）。
- 一个文件算一个主体，主体名默认取文件名；`index` / `main` / `page` / `list` / `home`
  这类入口文件改取父目录名（`views/visitor/index.vue` → `visitor`）。
- 文件里必须至少含 3 条**非泛用中文串**才算一个主体（`GENERIC_TOKENS` 里那些
  「用户 / 状态 / 数据 / 功能」不算）。这条决定了**生成的文件必须带真实中文业务文案**，
  纯英文代码派不出主体。
- 依赖目录、构建产物目录一律不参与派生。

所以默认 spec 的每个模块都会同时产出前端页面、后端接口、业务规则三处代码，
并且三处都带模块自己的中文业务词。主体数 = 18 个业务模块 + 若干公共文件 ≈ 29。

## 规则同步

`repo_rules.py` 是出题闸门口径的**冻结快照**（抄写日期 2026-09-22），本 skill 靠它自给自足。
如果目标机器上已经有出题闸门脚本，可以顺手比对一次，别让快照悄悄过期：

```bash
python3 scripts/verify_repo.py --rules-drift <任一份外部出题闸门脚本的路径>
```

这一步是可选的：没有这份脚本时本 skill 照常独立工作。比对项是常量级相等，
所以两边口径只要有一处不同就会报出来。

输出 `ok: false` 时按上面列的常量名逐项更新 `repo_rules.py`，再更新 `GATE_VERSION`，
然后重跑 `tests/test_gates.py`。比对项：`REPO_SOURCE_PATTERNS`、`PARENT_NAMED_STEMS`、
`GENERIC_DIR_NAMES`、`GENERIC_TOKENS`、`IGNORED_SOURCE_DIR_PARTS`、`DEMAND_MODE_LEXICON`。

## 生成出来的仓库还要满足的工程约定

- 前端 dev server：Vite 写 `server.open = false`；若换成 CRA / Webpack，
  对应写 `BROWSER=none` 或 `devServer.open: false`。`verify_repo.py` 会扫这三处。
- `.gitignore` 必须在仓库根目录，且覆盖：`node_modules/`、`dist/`、`build/`、`target/`、
  `__pycache__/`、`.venv/`、包管理器缓存、`.vite`/`*.tsbuildinfo`、日志、`.env*`、
  覆盖率目录、IDE 目录、`.DS_Store`。
- 依赖目录（`node_modules`、`.venv` 等）一旦落地，必须被 `.gitignore` 命中，
  且不出现在 `git status --porcelain` 里——这条会被 `git check-ignore` 实测。
- 根目录带 `docker-compose.yml`（含 `services`）、`README.md`、`Makefile`，
  方便下游按「已容器化，可一键起环境」判定环境可复现等级。
