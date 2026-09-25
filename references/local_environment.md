# 本机环境（已实测，2026-09-22）

本文件记录本 skill 在**这台机器**上实际验证过的路径与工具版本。
跑生成、验收、真启动探针、推 GitHub 之前先读这里，不要猜路径、不要猜命令名。

## 平台

| 项 | 值 |
|---|---|
| 操作系统 | macOS（darwin） |
| 默认 shell | zsh |
| 磁盘可用 | 约 265 GB（`/System/Volumes/Data`） |

## 本 skill 的安装位置

```text
~/.kunlunxiaozhi/skills/cc-repo-create
```

下文用 `<skill>` 指代这个目录。skill 是自洽的：所有脚本都在 `<skill>/scripts/` 下，
不依赖任何其他 skill、也不依赖 `~/.codex/`。

## 运行时（全部走 PATH，无需写绝对路径）

| 工具 | PATH 上的实际来源 | 版本 | 备注 |
|---|---|---|---|
| `python3` | `~/.workbuddy/binaries/python/versions/3.13.12/bin/python3` | 3.13.12 | 隔离运行时；`pip` 26.0 可用；`venv` 可用 |
| `node` | `~/.workbuddy/binaries/node/versions/22.22.2/bin/node` | v22.22.2 | 隔离运行时 |
| `npm` | `~/.workbuddy/binaries/node/versions/22.22.2/bin/npm` | 10.9.7 | 生成/构建前端用它 |
| `git` | Apple Git | 2.50.1 | 生成器的 `--git-init`、探针的 `git clone` 都靠它 |
| `gh` | `~/.local/bin/gh` | 2.95.0 | 已登录账号 `fengyin-solo`（`gh api user -q .login` 实测返回该值） |

`python3` / `node` / `npm` 都已在 PATH 上指向隔离运行时，**直接写 `python3`、`npm` 即可**，
不要硬编码 `/Users/...` 绝对路径（写死的路径换台机器必炸，也会被 `verify_repo.py` 拦下）。

## 没有装的东西（会直接影响验收结论）

| 工具 | 状态 | 影响 |
|---|---|---|
| JDK 17 | **未安装**（`/usr/bin/java` 只是报错的占位 stub，`/usr/libexec/java_home` 找不到运行时） | — |
| Maven (`mvn`) | **未安装** | — |

后果：**Java 后端（Spring Boot 版）只能生成，无法在本机真启动验收。**
`probe_startup.py` 遇到 Java 后端会明确报「本机没有 JDK/Maven，无法真启动」，
这一条按**未验证**处理，不要写成已通过。

要出题、要交付，**本机请统一用 `--backend python`（FastAPI）**；
确实需要 Java 版时，先在这台机器上装 JDK 17 + Maven，或把 G8 标成未验证再交付。

## 命令注意事项

1. **路径带中文或空格必须加引号。** 本机的工作目录就带中文和空格，例如
   `/Users/fengyin/项目/字节/ccGsb/任务目录 2`，所有命令里的这类路径都要用双引号包起来：

   ```bash
   python3 <skill>/scripts/scaffold_repo.py \
     --parent "/Users/fengyin/项目/字节/ccGsb/任务目录 2" \
     --spec <skill>/examples/spec-film-production.json --git-init
   ```

2. **`<skill>` 要展开成绝对路径**，因为 `--parent` 模式下脚本不假设 cwd。
   注意 `--parent` 生成的是**两层** `<编号>/<编号>`，`--repo` 要传内层那个仓库根：

   ```bash
   python3 ~/.kunlunxiaozhi/skills/cc-repo-create/scripts/verify_repo.py \
     --repo "/Users/fengyin/项目/字节/ccGsb/任务目录 2/cc10001/cc10001"
   ```

3. **推 GitHub** 用本机已登录的 `gh`（账号 `fengyin-solo`），默认 owner 会自动取到它，
   不用显式传 `--owner`；想推到别的账号/组织再加 `--owner`。

4. **真启动探针会联网**：`probe_startup.py` 会 `npm install` 与 `pip install`，
   需要能访问 npm registry 与 PyPI。离线时探针的 G8 会失败，这属于环境问题、不是仓库问题。

5. **探针日志**默认落在「被验仓库的父目录」下的 `.probe-logs/<仓库名>/`；
   本机父目录若在中文字路径下同样能正常写入。

## 与远端同步（更新 / 推送）

本目录已是 `https://github.com/fengyin-solo/cc-repo-create.git` 的 git 工作区：
分支 `main` 跟踪 `origin/main`，`origin` 地址**不带 token**（认证交给本机 `gh` 与 git 凭据，
token 不会落到 `.git/config` 里）。

```bash
cd ~/.kunlunxiaozhi/skills/cc-repo-create

# 1) 拉远端更新（只允许快进，避免把本机改动冲掉）
git pull --ff-only

# 2) 提交本机改动
git add -A && git commit -m "..."

# 3) 推送
git push
```

约定：

- **只用 `--ff-only` 拉取。** 本地与远端分叉时它会直接失败，这时先 `git status` 看清楚，
  **不要 `--force`**——强推会把远端历史覆盖掉。
- `.gitignore` 已挡住 `__pycache__/`、`.probe-logs/` 等运行时产物；
  `git status` 里出现这些说明忽略规则被破坏了，先修 `.gitignore` 再提交。
- 改过 `scripts/` 或 `tests/` 后，提交前先跑上面的「快速自检」。
- 本机专属内容（本文档里的 `/Users/fengyin/...` 路径、`SKILL.md` 的「本地环境」小节）
  也在版本控制里。若要把这份 skill 分发到别的机器，先确认这些内容是否需要清掉。

## 快速自检（换机器后先跑这一条）

```bash
python3 -m unittest discover -s ~/.kunlunxiaozhi/skills/cc-repo-create/tests -v
```

用例全绿说明脚本与本机 Python/Node/git/gh 对得上。
