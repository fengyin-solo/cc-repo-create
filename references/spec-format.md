# spec 格式：怎么描述一个要生成的应用

生成器只认一份 JSON。默认 spec 在 `examples/spec-park-pass.json`（园区通行与安全运营平台，
18 个业务模块）。换题域就复制一份改内容，不要改脚本。

## 顶层字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `app_name` | ✓ | 应用中文名，进标题、README、页面头部 |
| `app_slug` | ✓ | 英文短名，做包名与前端 package name；只能用字母数字与连字符 |
| `app_desc` | ✓ | 一句话业务描述，进页面头部与 README |
| `frontend` | | `vue`（默认）或 `react`，可被 `--frontend` 覆盖 |
| `backend` | | `python`（默认，FastAPI）或 `java`（Spring Boot 17），可被 `--backend` 覆盖 |
| `port_frontend` | | 前端端口，默认 5173 |
| `port_backend` | | 后端端口，默认 8000 |
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
| `key` | ✓ | 英文小写标识；同时是目录名、接口前缀、主体名。**不能与内置名撞车后又按名字导入**（生成器已用别名导入规避，但别用 Python 关键字） |
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
<父目录>/<项目编号>/         例如 cc10001 / YEL10003，编号规则见 SKILL.md「项目编号」
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

## Java 后端的额外前提

Java 版生成的是 Spring Boot 3 + Maven 工程（`pom.xml`、`Application.java`、
`controller/*Controller.java`、`service/*Service.java`）。**本 skill 无法在没装
JDK 17 + Maven 的机器上真启动它**，此时 `probe_startup.py` 会明确报
「本机没有 JDK/Maven，无法真启动」，这一条按未验证处理，不要写成已通过。
