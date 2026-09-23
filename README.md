# cc-repo-create

生成**可直接出题、也能直接交付**的全栈代码仓库的 Agent skill（已适配本机，见 `references/local_environment.md`）。

给一个题域，它产出一个前后端齐全、在干净克隆里真能起来的仓库：业务模块同时落到前端页面、
后端接口和业务规则三处，行数、主体数、需求模式覆盖度都按出题容量算好，不用生成完再回头补。

## 产出长什么样

```text
cc10001/
├── frontend/                Vue 3 或 React + Vite + TypeScript
│   ├── src/views/<模块>/     每个业务模块一个页面
│   ├── src/router/           路由
│   └── vite.config.ts        server.open = false（不自动打开页面）
├── backend/                 FastAPI（Python）或 Spring Boot（Java 17）
│   ├── app/routers/<模块>.py  每个模块一组接口
│   ├── app/services/<模块>.py 状态流转与字段校验
│   └── app/store.py          内存仓库 + 示例数据
├── .gitignore               依赖 / 缓存 / 构建产物 / 日志 / 环境文件全覆盖
├── README.md
├── docker-compose.yml
└── Makefile
```

## 硬性要求（全部机器复核，不靠人眼）

| 闸门 | 要求 |
|---|---|
| G1 结构 | 前端 Vue 或 React、后端 Java 或 Python，双端各有依赖清单与启动入口 |
| G2 启动 | 前端 dev server 关掉「自动打开页面」 |
| G3 忽略 | 根目录 `.gitignore` 覆盖依赖、缓存、构建产物、日志、环境文件 |
| G4 体量 | 源码 >= 2000 行（不含依赖目录、构建产物、锁文件） |
| G5 容量 | 出题容量 >= 30 条（主体数 × 每主体上限，需求模式数 × 每模式上限，再与单仓库上限取小） |
| G6 合规 | 不含禁用题型（`代码理解`）、无密钥、无超大文件、无违规业务域，**且题域不命中 0731 严禁出题清单** |
| G7 环境 | Python 全量可编译、后端入口能 import、依赖都声明、没有写死本机路径 |
| G8 真启动 | **在干净克隆里**装依赖、起服务、打开页面、验代理链路、跑生产构建 |

容量公式里「可用需求模式 12 类 × 每模式上限」是硬上限：每模式 2 条时只有 24 个席位，
**任何仓库都到不了 30 条**；所以出题链路把每模式上限默认值设为 3（36 个席位）。

## 用法

```bash
# 0) 定完 spec 先自检题域（命中 0731 严禁出题清单就换方向，没有绕过参数）
python3 scripts/scaffold_repo.py --check-spec --spec <spec.json>

# 1) 生成（按项目编号自动命名：cc10001 / cc10002 / YEL10003 …）
python3 scripts/scaffold_repo.py --parent <父目录> --spec <spec.json> --git-init

# 2) 静态验收
python3 scripts/verify_repo.py --repo <仓库>

# 3) 真启动验收（干净克隆里装依赖、起服务、打开页面、跑构建）
python3 scripts/probe_startup.py --repo <仓库> --clean-clone --build

# 4) 推到 GitHub（默认 dry-run，确认后加 --execute --confirm PUBLISH）
python3 scripts/publish_github.py --repo <仓库>
```

**严禁题域**：题域命中 0731 严禁出题清单（游戏/图形、本地桌面工具、电商库存工单预约这类
平台业务系统、记账看板这类数据可视化）就是废题，仓库做得再合规也没用。生成前与验收时各拦一次，
都不给绕过参数。细节见 [references/spec-format.md](references/spec-format.md) 与
[references/hard-rules.md](references/hard-rules.md)。

生成的仓库**默认建成 public**（下游容器要能匿名 clone）。脚本不会只靠「建的时候传了 public」
就算数：推送前后各读一次真实可见性，发现还是 private 就改成 public 并记录前后值，改不动直接报错。

### 项目编号

`--parent` 模式下，目录名与仓库名都取项目编号：`<前缀 2~4 字母><5 位数>`，5 位数首位固定是 1。

- 前缀默认 `cc`，`--prefix YEL` 得到 `YEL10001`；大小写按传入原样使用。
- 编号接着当前目录已有的往后排，换前缀不重置序号。
- GitHub 上已占用的编号自动跳过；查不到 GitHub 时回退成本地序号并标记 `github_checked: false`。

## 目录结构

```text
SKILL.md                 skill 正文（工作流 + 硬规则）
scripts/
├── scaffold_repo.py     生成器：Vue / React × Python / Java 四种组合 + 编号分配
├── verify_repo.py       静态验收 G1–G7，非 0 退出码即不通过
├── probe_startup.py     真启动探针 G8（支持 --clean-clone / --build）
├── publish_github.py    建仓库并推 main（只允许快进，拒绝强推）
└── repo_rules.py        冻结快照：容量口径 + 严禁题域清单
references/
├── hard-rules.md        闸门口径、容量推导、严禁题域、推送安全边界
└── spec-format.md       spec 字段说明、严禁题域、怎么写出撑得起 30 条的题域
examples/
└── spec-film-production.json  默认题域：影视剧组拍摄制作管理平台（20 个业务模块）
tests/                   56 条自测，含各闸门的负例
└── fixtures/            历史反例：园区通行 spec（含工单/库存/权限/预约，现已被拒）
```

## 自测

```bash
python3 -m unittest discover -s tests -v
```

## 依赖

- Python 3.10+
- 生成 Vue/React 项目需要 Node.js 与 npm；生成/启动 Java 后端需要 JDK 17 + Maven
- 推送 GitHub 需要 `gh`（已登录）或 `GH_TOKEN` / `GITHUB_TOKEN`

本 skill 自带全部规则快照与脚本，不依赖其他 skill 也能独立运行。
