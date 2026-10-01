# spec 格式：怎么描述一个要生成的应用

生成器只认一份 JSON。默认 spec 在 `examples/spec-film-production.json`（影视剧组拍摄制作管理平台，
20 个业务模块）。换题域就复制一份改内容，不要改脚本。

**改完先过一遍严禁题域自检**（命中即废题，没有绕过参数）：

```bash
python3 <skill>/scripts/scaffold_repo.py --check-spec --spec /path/to/spec.json
```

## 严禁题域：这些方向不能选

清单来自 `gsb-creator/references/0731_方案规则.md` 第 6 节，抄进本 skill 的
`scripts/repo_rules.py`。四类**整类禁用**，不是「换个说法就行」：

| 类别 | 点名方向 |
|---|---|
| 🎮 游戏与图形 | 贪吃蛇、打砖块、俄罗斯方块、坦克大战及变种；粒子/物理模拟；塔防/2D 解谜/潜行/平台跳跃；喂食小动物；记忆翻牌/连连看；五子棋等棋类；2048；扫雷；打地鼠 |
| 🖥️ 本地/桌面工具 | 命令行 CLI、代码片段管理器、批量重命名、截图标注、文件管理/同步、书签、密码管理器 |
| 🏢 平台/业务系统 | 电商购物车/订单、RBAC 权限后台、仓库库存、投票问卷、考勤 OA、图书借阅、博客 CMS、医院挂号/问诊、外卖点餐、CRM、IM 私信、拍卖、停车场、工单客服、商品+Excel 导入、积分商城、XXX 预约系统 |
| 📊 数据可视化/前端页面 | 报表统计、Streamlit、CSV 看板、记账、健康健身、菜谱、天气、番茄钟、习惯打卡、音乐播放器、旅行日记、观影记录 |

最坑的是第三类：`库存` / `工单` / `预约` / `权限` 这些词在很多正常业务里都是顺手的模块名，
但只要出现在 spec 的应用名、描述、模块名、业务字段、状态或动作里，生成就直接被拒——
而且生成的 README 会带一张「模块 | 目录 | 业务对象 | 主要字段」的表，模块 key 与字段都会
在 README 里露出来，下游 `check_repo.py` 一读就是 BLOCK。

**换方向，别改字眼。** 把「库存」写成「在库」只是绕过关键词，题域还是那个题域。

### 已经被验证可用的方向

这几个方向都能凑够 16+ 个彼此独立的业务对象，且不碰清单：

- **文化制作**：影视剧组拍摄（剧本分场 / 通告 / 场地 / 器材 / 后期特效 / 杀青结算）——本 skill 的默认 spec 就是这个
- **工业运维**：光伏电站、污水处理厂工艺、轨道交通信号检修、特种设备点检润滑
- **科研检测**：实验室样品与检测、地质勘探、气象观测站网
- **基础设施养护**：市政道路桥梁、城市地下管网、园林绿化
- **物流作业**：冷链温控、港口集装箱作业、机场地面保障（**注意别带「库存」字样的模块**）
- **田野记录**：考古发掘现场（探方 / 地层 / 遗迹单位 / 出土物 / 浮选 / 测年送检）
- **防灾巡查**：地质灾害隐患点（形变与裂缝观测 / 雨量预警 / 避险搬迁 / 治理工程验收）

已经出过的方向不要重复挑，换一批主体更划算。挑域时先自问一句：**这个题域本身是不是在清单里？**
是就换，不要试图靠措辞绕开。另外两个高频踩点：`派工单` 里的「工单」、`天气状况` 里的「天气」，
都会被严禁清单直接命中，写成「出工记录」「当日气候」才是换题域而不是改字眼——业务对象本身也换了。

## 顶层字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `app_name` | ✓ | 应用中文名，进标题、README、页面头部 |
| `app_slug` | ✓ | 英文短名，做包名与前端 package name；只能用字母数字与连字符 |
| `app_desc` | ✓ | 一句话业务描述，进页面头部与 README |
| `frontend` | | `vue`（默认）或 `react`，可被 `--frontend` 覆盖 |
| `backend` | | `python`（默认，FastAPI）、`java`（Spring Boot 17），或 `none`（**纯前端仓库**，不生成后端）；可被 `--backend` 覆盖 |
| `port_frontend` | | 前端端口，默认 5173 |
| `port_backend` | | 后端端口，默认 8000（`backend: none` 时不使用） |
| `modules` | ✓ | 业务模块数组，**至少 16 个**才能保证容量 >= 30 |

## 模块字段

```json
{
  "key": "visitor",
  "name": "访客预约",
  "entity": "访客预约单",
  "fields": ["预约单号", "访客姓名", "联系电话", "来访事由", "通行区域", "有效时段", "接待人员"],
  "statuses": ["待审批", "已通过", "已拒绝", "已失效"],
  "actions": ["提交审批", "发放通行凭证", "撤销预约"],
  "action_targets": ["待审批", "已通过", "已失效"],
  "metrics": ["今日预约", "待审批数量", "逾期未离场"],
  "desc": "可选，覆盖自动生成的页面说明"
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `key` | ✓ | 英文小写标识；同时是目录名、接口前缀、主体名。**不能与内置名撞车后又按名字导入**（生成器已用别名导入规避，但别用 Python 关键字）；**也不能叫 `dist` / `build` / `out` / `target` / `coverage` / `logs` / `node_modules` 这些依赖与构建产物目录名**——它会变成 `frontend/src/views/<key>/`，被 `.gitignore` 吃掉，页面进不了仓库（生成前会直接拒，见 `RESERVED_MODULE_KEYS`） |
| `name` | ✓ | 模块中文名，进页面标题与导航 |
| `entity` | ✓ | 业务对象中文名，进按钮与提示语 |
| `fields` | ✓ | 业务字段中文名，**至少 3 个**（既是表格列，也是主体识别要用的中文串） |
| `statuses` | ✓ | 状态枚举；第一条是新建时的默认状态 |
| `actions` | ✓ | 可执行动作，进前端按钮与后端动作接口 |
| `action_targets` | | 与 `actions` 一一对应的目标状态；不写就按 `statuses` 顺序往后推一格 |
| `metrics` | | 页面顶部三张指标卡的名字 |
| `desc` | | 覆盖自动生成的页面说明 |

## 怎么写出「撑得起 30 条」的 spec

1. **模块数 >= 16**。少于 16 个主体，容量公式里的「主体数 × 2」就先卡住了。
2. **每个模块的 `fields` 写 6~8 个业务字段，别写「用户名 / 状态 / 数据」这类泛词**。
   主体识别会过滤掉 `GENERIC_TOKENS` 里的泛用词，泛词写多了这个文件派不出主体。
3. **`fields` / `statuses` / `actions` 要用具体业务词**：`访客预约`、`门禁授权`、`逾期未离场`
   这种能撑起「规则与阈值」「状态流转」「边界与空态」等多种需求模式的题面；
   `数据管理`、`信息维护` 这种则会把所有题挤进同一格。
4. **模块之间业务对象要拉开**。同一主体 + 同一需求模式只能出一条题，
   16 个彼此独立的业务域比 16 个「XX 管理」更经得起出题。
5. **动作至少 2~3 个且方向不同**（正向推进 + 打回/作废），这样「状态流转」和
   「缺陷处置」两类题都有落点。

## 生成出来的目录

```text
<父目录>/<项目编号>/<项目编号>/         例如 cc10001/cc10001、YEL10003/YEL10003
├── frontend/                 Vue 3 或 React + Vite + TypeScript
│   ├── src/views/<key>/index.vue|tsx   每个模块一个页面（主体 = <key>）
│   ├── src/router/index.ts             React 走 src/App.tsx
│   ├── src/api/client.ts               统一请求封装
│   ├── src/stores/session.ts           会话状态（React 版省略）
│   ├── vite.config.ts                  server.open = false
│   └── package.json / index.html / tsconfig.json
├── backend/                  FastAPI 或 Spring Boot
│   ├── app/routers/<key>.py            每个模块一组接口（主体 = <key>）
│   ├── app/services/<key>.py           状态流转与字段校验
│   ├── app/store.py / seed.py          内存仓库与示例数据
│   └── requirements.txt / run.sh
├── .gitignore
├── README.md
├── docker-compose.yml
└── Makefile
```

**`backend` 写成 `none` 时是纯前端仓库**，上面的 `backend/` 整块不存在，
前端多出本地数据层：

```text
├── frontend/
│   ├── src/views/<key>/index.vue       每个模块一个页面（Vue：主体 = <key>）
│   ├── src/views/<key>/<Key>View.tsx   React：组件落在这里（主体 = <Key>View）
│   ├── src/views/<key>/index.tsx       React：只写一行 re-export（barrel）
│   ├── src/api/local-service.ts        列表 / 筛选 / 动作流转 / 导出 / 概览
│   ├── src/data/modules.ts             模块元数据：字段、状态、动作、流转目标
│   ├── src/data/seed.ts                示例数据（每模块三行）
│   ├── src/data/local-store.ts         localStorage 持久化
│   ├── src/data/types.ts               公共类型
│   └── vite.config.ts                  server.open = false，且**没有 /api 代理**
├── .gitignore
├── README.md
├── docker-compose.yml                  只有 frontend 一个 service
└── Makefile
```

`frontend/package.json` 里的 `repoKind` 字段（`fullstack` / `frontend-only`）就是形态标记，
`verify_repo.py` 与 `probe_startup.py` 都读它，不要手工删。

**外面那层 `<项目编号>` 是任务容器，里面那层才是仓库根**：`.git`、`.gitignore`、`frontend/`、
`backend/` 全在内层。`scaffold_repo.py --parent` 的输出里 `out` / `repo_root` 指内层、
`project.container` 指外层；`verify_repo.py`、`probe_startup.py`、`publish_github.py` 的
`--repo` 都传内层那个路径。用 `--out` 显式给路径时不套这一层，给的路径就是仓库根。

## Java 后端的额外前提

Java 版生成的是 Spring Boot 3 + Maven 工程（`pom.xml`、`Application.java`、
`controller/*Controller.java`、`service/*Service.java`）。**本 skill 无法在没装
JDK 17 + Maven 的机器上真启动它**，此时 `probe_startup.py` 会明确报
「本机没有 JDK/Maven，无法真启动」，这一条按未验证处理，不要写成已通过。
