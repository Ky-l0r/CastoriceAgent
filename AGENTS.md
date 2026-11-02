# AGENTS.md

本文件是 CastoriceAgent 仓库对**所有 AI 编码代理**（WorkBuddy、Claude Code、Codex、Cursor、Copilot 等）的强制约定。
人类贡献者请读 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 一、这份文件怎么用

1. 每次开工前先读本文件，再按需读：
   - [CONTRIBUTING.md](CONTRIBUTING.md)：规范细节、目录归属、技术债清单
   - [docs/README.md](docs/README.md)：文档索引
   - [docs/refactor/](docs/refactor/)：四期改造的设计与验证记录，**解释"为什么代码长这样"**
   - [tests/README.md](tests/README.md)：测试基线的来历与运行方式
2. 冲突时的优先级：**用户当前指令 > 本文件 > CONTRIBUTING.md / docs/\* > README.md**。
   发现冲突要报告，不得静默选一边。
3. 本文件不是建议，是门禁。做不到时停下来问，不要绕过。

---

## 二、一次任务的完成定义

"改完了"不等于完成。以下六项全部达成才算完成，缺哪项就在汇报里明说卡在哪：

1. 改动已实现，范围与用户要求一致，没有夹带。
2. 本地验证通过，汇报里贴的是**真实命令输出**。
3. 文档已按需同步（见第八章）。
4. 已提交，提交信息符合 `type(中文scope): 摘要`。
5. 已推送，或按用户要求留在本地并说明原因（见第十章）。
6. 已向用户汇报：改了什么、为什么、验证结果、未做与风险。

---

## 三、开工前三件事

### 3.1 看工作区状态

```bash
git status --short && git branch --show-current
```

**本仓库经常存在他人（或上一轮任务）的未提交改动。** 如果 `git status` 里有与本次要求无关的文件：

- 不要顺手把它们一起提交；
- 不要把它们的改动当成自己的；
- 无法判断归属时**先问**，不要自作主张 stash 或 checkout。

### 3.2 认清本机环境（2026-11-03 实测，换机器需重新确认）

| 现象 | 原因 | 正确做法 |
| :--- | :--- | :--- |
| `python -m pytest` 报 `No module named pytest` | PATH 上的 `python` 是工具链自带的 3.13，**没装本项目依赖** | 用 `py -3.13 -m pytest`（Windows 启动器指向系统 Python，已装 pytest 9.1.1） |
| 前端 `engines` 要求 Node `>=24 <25`，本机是 v22.22.2 | 声明与实际不符，历史遗留 | 现有依赖可正常构建；**不要因此改 `engines`**，也不要随意重装 node_modules |
| `git push` / `git fetch` 报 `CRYPT_E_NO_REVOCATION_CHECK` | 本机 TLS 证书吊销检查失败，**不是网络问题** | 见 10.4，用 `git -c http.sslBackend=schannel ...` |
| `powershell` 版本是 5.1 | 那是 Windows PowerShell，不是 PowerShell 7 | 打包用 `pwsh`（本机实测 7.6.9） |

### 3.3 确认基线是绿的

```bash
py -3.13 -m pytest -q      # 期望全绿；当前基线 798 passed, 1 skipped（约 2~3 分钟）
ruff check .
ruff format --check .
```

基线本身是红的，说明工作区已有别人没修完的问题：**先报告，不要顺手修**。

---

## 四、不要过度发挥

本章是最重要的部分。项目要的是**准确完成被要求的那一件事**，不是看起来更专业。

### 4.1 默认行为

- 只改与本次要求直接相关的文件。改到第三个无关文件时停下来问。
- 不顺手重构、不顺手格式化全库、不顺手改名、不顺手升级依赖、不顺手加抽象层、不顺手加配置开关。
- 不为"以后可能需要"预留扩展点，需要时再加。
- 不引入新依赖去解决手写十行就能解决的问题（本项目的依赖体积直接影响打包大小）。
- 不把简单逻辑包装成 Manager + Adapter + Strategy 三层。
- 不写"为将来的并发优化预留"这类空壳代码。

### 4.2 不许编造

- 不许声称跑过没跑的命令，不许编造测试数字、构建耗时、CI 结论。
- 没验证的写"未执行"或"未验证"，并说明原因（例如需要真机、需要真实 API Key）。
- 测试数、打包体积这类易过期数字，写的时候标注实测日期。
- 不确定某个函数是否存在、某个配置键是否生效时，**去读源码或跑命令**，不要凭印象写。

### 4.3 需求缺口：问，不要猜

- 用户只描述了 80% 时，问清剩下 20%，不要用"通常都这么做"补齐。
- 同一需求有多种合理解法且代价不同（改界面还是改桥接、加配置项还是写死、改 `ai.py` 还是进 `castorice/`）时，
  列出 2-3 个选项与各自代价，让用户决定。
- 涉及**产品行为**的一律问：角色语气、提示词口径、表情包触发策略、主动聊天的投递时机。
  这些是产品本身，不是实现细节，不许自行发明。
- 用户的要求会触碰本文件的硬性约束时，先指出冲突，等指示。

### 4.4 什么情况可以自行决定

- 拼写、标点、明显笔误、格式化、import 顺序、注释措辞。
- `ruff check` / `tsc` 报错的直接修法（不改语义的前提下）。
- 已明确要求改动的必要配套（新增 RPC 要同步前端调用、新增配置项要进 `example_config.yaml`）。
- 本文件或 docs 已写明处理方式的重复问题。

### 4.5 怎么问

- 一次问完，不要挤牙膏；给出选项与默认建议。
- 问题里包含：你要做什么、卡在哪、可选方案、各自代价。
- 问完就停。不要一边提问一边按自己的猜测把代码改了。

### 4.6 不做「半件」

- 一个需求如果只能做一半就会造成前后不一致（例如只改后端 RPC 不改前端调用、
  只改提示词不改 `example_config.yaml`），先停下来把另一半的代价说清楚，等指示。
- 同类问题第二次出现时，优先改规范或抽公共件，而不是再复制一份实现。

---

## 五、代码规范

`ruff`（Python）与 `tsc`（前端）是硬门禁，CI 会拦。以下是需要人（和代理）判断的部分。

### 5.1 Python

- **规则配置只有一处**：`pyproject.toml` 的 `[tool.ruff]`。不要在别处加 flake8 / pylint / mypy 配置。
- 提交前 `ruff check .` 与 `ruff format --check .` 必须干净（pre-commit 会自动跑）。
- 行长 100，双引号，LF，4 空格；中文注释与全角标点**允许**（`RUF001-003` 已关）。
- 类型注解用 `typing` 风格（`Optional[X]` / `List[X]`），**不要**写 `X | None`：
  项目最低支持 Python 3.9，运行期求值会失败。`UP006/UP045/UP035` 已在配置里豁免。
- 加 `# noqa` 必须带原因：`# noqa: S112 - 单行脏数据跳过，不中断整批历史`。
- 异常不得吞成 `None` 继续执行；兜底降级处要写明降级语义（日志至少一条 debug）。
- 新增 `except Exception` 会被 `BLE001` 拦（当前豁免是历史债），**新代码请捕获具体异常**。

### 5.2 前端

- TypeScript 严格模式；三个 tsconfig 分别对应渲染进程 / 主进程 / preload，改完都要过：
  `pnpm run check:renderer && pnpm run check:main && pnpm run check:preload`
- 样式用 Tailwind 工具类 + `src/styles/global.css` 的 CSS 变量，不引入新的样式方案。
- 格式标准见 `webapp/.prettierrc`（`pnpm run format`）。历史文件尚未整体重排，
  **新代码按配置写**，不要顺手全量格式化前端。
- 组件放 `src/components/`，设置页各分类放 `src/components/settings/`，页面放 `src/pages/`。

### 5.3 分层与依赖方向

依赖只能单向：`webapp` → `bridge.py`（RPC）→ `ai.py` → `castorice/`。**禁止反向。**

| 层 | 路径 | 职责 | 禁止 |
| :--- | :--- | :--- | :--- |
| 界面层 | `webapp/src/`、`webapp/electron/` | React UI、Electron 壳、托盘、通知 | 直接持有 API Key 或 bridge token |
| 桥接层 | `castorice/bridge.py` | WebSocket/JSON-RPC 服务、事件广播、RPC 分发 | **import `ai`**；包含任何界面代码 |
| 门面层 | `ai.py` | 配置、提示词、记忆、提供商、工具、表情包；对外契约 | 被 `castorice/` 反向 import |
| 内核层 | `castorice/` | 四模式、会话、harness 循环、权限、工具、记忆引擎、渠道 | import `ai`；import 任何 Qt 类（仅允许运行期延迟导入） |
| 渠道层 | `castorice/channels/` | NapCat 等外部消息通道 | 在通道里做业务决策 |

`castorice/bridge.py` 不 import `ai` 是**结构性红线**：它靠注入的 `bot` 对象工作，
由组合根（`webapp/electron/sidecar/spawn-bridge.py`）装配。破坏它会引入循环依赖。

### 5.4 本项目特有红线

1. **提示词体系是产品行为**：`prompts/` 下的 `.md` 决定她怎么说话。
   - 改提示词要在提交信息里说明"改了什么语气/设定"，不要混在功能提交里。
   - 条目级语法（`- 触发词:` / `- 常驻:` / `- 优先级:`）是解析契约，
     改动必须同步 `ai.py` 的 `PromptModule` 与 `tests/test_prompt_triggers.py`。
   - `prompts/modes/` 是各模式的分层提示词，缺文件时走兜底，不要用空文件占位。
2. **权限与沙箱是安全边界**：不得为了让某个功能"跑通"而放宽
   `castorice/permissions.py` 的沙箱级别、命令白名单或审批策略。
   工具限制是策略级隔离，故意保守；需要放宽时先问。
3. **桥接协议是前后端共享契约**：新增/修改 RPC 要同时改
   `castorice/bridge.py`（后端）、`webapp/src/lib/`（前端调用）、`tests/test_bridge.py`，
   必要时更新 `tests/README.md` 的用例计数说明。
4. **会话与配置格式必须向后兼容**：
   - `sessions/` 的 JSONL 是 append-only，旧日志要能读（脏行跳过，不整体失败）。
   - `config.yaml` 写回必须**保留原有注释与排版**（`update_yaml_scalars` 系列），
     禁止整文件重写；写坏要放弃写入而不是覆盖。
   - `tests/test_backward_compat.py` 是这些契约的护栏，**不得为了让它通过而放宽断言**。
5. **离线可跑是测试纪律**：所有用例默认禁止联网（`tests/conftest.py` 的 autouse 夹具）。
   需要真实网络的用例加 `@pytest.mark.allow_network`，且不能成为唯一覆盖手段。
6. **数据落点**：运行期数据只写程序目录（`database/`、`memory/`、`sessions/`、`logs/`、
   `channels/`、`schedules.json`），不得写系统目录、注册表或用户其它位置。

### 5.5 注释

- 注释解释"为什么"，不解释"是什么"。
- 绕过上游缺陷、与直觉相反的写法、有取舍的实现，必须写清原因。
- 用中文；可沿用源码里已有的 `# ---- 分组 ----` 风格，不加装饰性分隔线。

---

## 六、文件管理

### 6.1 目录职责

| 目录 | 放什么 |
| :--- | :--- |
| `ai.py` | 对外门面：配置、提示词、记忆、提供商适配、工具注册、表情包、`AI` 主类 |
| `castorice/` | 四模式内核（`modes.py`）、agent 循环（`harness.py`）、会话（`session.py`）、权限（`permissions.py`）、调度（`scheduler.py`）、通知（`notify.py`）、桥接（`bridge.py`） |
| `castorice/tools/` | 工具集：fs / command / web / document / notes / life / mcp |
| `castorice/memory/` | 记忆引擎：embedding / wiki / summary / worldbook / entries |
| `castorice/providers/` | 提供商适配与能力判断 |
| `castorice/channels/` | 外部消息通道（NapCat / OneBot） |
| `webapp/` | Electron 壳 + React UI（`electron/` 主进程与 preload，`src/` 渲染进程） |
| `prompts/` | 角色设定（11 个模块）+ `modes/` 分层提示词 |
| `tests/` | 回归测试，文件名对应模块 |
| `scripts/` | 打包与依赖安装脚本（PowerShell + Python） |
| `docs/` | 维护者文档（设计、验证、契约）；用户文档在根 `README.md` |
| `build-specs/archive/` | 历史 PyInstaller `.spec`，**不参与构建** |
| `Image/` | 头像、图标、表情包（文件名即表情名） |

### 6.2 新文件放哪（按顺序判定）

1. 内核能力（工具、记忆、权限、渠道） → `castorice/` 对应子包。
2. 前端 ↔ 后端的新 RPC → `castorice/bridge.py`（不要新建服务）。
3. 界面组件 → `webapp/src/components/`；页面 → `webapp/src/pages/`。
4. 角色设定文本 → `prompts/`。
5. 测试 → `tests/test_<模块>.py`。
6. 构建/发布脚本 → `scripts/`。
7. 设计文档 → `docs/`；用户可见说明 → 根 `README.md`。
8. 都不合适 → **先问**，不要新建顶层目录。

### 6.3 入库 / 不入库

**绝不入库**（`.gitignore` 已覆盖，仍要自查）：

- `config.yaml`：**含真实 API Key**，一旦提交必须立即停止并告知用户，不要自行改写历史
- `database/`、`memory/`、`sessions/`、`logs/`、`channels/`、`schedules.json`、`ui_settings.json`：
  用户对话与记忆，属隐私
- `build/`、`dist/`、`发布包/`、`webapp/dist/`、`webapp/release/`、`node_modules/`
- `.agent-teams/`（多智能体本地状态）、`.ruff_cache/`、`__pycache__/`
- `Cyrene-Agent-master/`（第三方参考副本，勿提交勿删除）

**必须入库**：源码、`prompts/`、`Image/` 素材、`scripts/`、`docs/`、`tests/`、
`example_config.yaml`（占位符，不含真实 Key）、`build-specs/archive/`（含其 README）。

> ⚠ **新增 `.gitignore` 规则时必须带前导斜杠**。
> 写成 `memory/` 会匹配任意层级的 `memory` 目录，把源码包 `castorice/memory/`
> 整个忽略掉——2026-11-03 就因此让 8 个源文件从未入库，clone 后项目无法启动。
> 正确写法是 `/memory/`。加完规则用下面两条命令自检：
>
> ```bash
> git check-ignore -v castorice/memory/store.py   # 应无输出（源码不该被忽略）
> git check-ignore -v memory/                     # 应输出命中的规则（数据该被忽略）
> ```

### 6.4 临时文件

- 需要脚本或中间产物时写到 `.tmp/`（已忽略），任务结束前删除。
- 不在仓库根目录新建 `test.py`、`debug.py`、`xxx_backup.md`、`repro2.spec` 之类文件。
  历史遗留的 `repro*.spec` 已归档到 `build-specs/archive/`，不要往根目录再放。
- 不留 `.bak`、`.orig`、注释掉的旧代码。

### 6.5 命名

- Python 模块小写下划线；类 PascalCase；常量 UPPER_SNAKE。
- 一个文件一个主职责，文件名与主类/主模块同名。
- 文档用中文名（与 `docs/` 现状一致）；测试用 `test_<模块名>.py`。
- 禁止 `新建文本文档.txt`、`副本`、`最终版`、`v2-final` 这类名字。

---

## 七、验证门禁

### 7.1 按改动分级

| 改动内容 | 必跑 |
| :--- | :--- |
| 纯文档、纯注释 | 不跑测试；检查链接有效、与代码是否仍一致 |
| 单个 `castorice/` 模块 | `ruff check .` + 对应 `tests/test_<模块>.py` |
| `ai.py`、`bridge.py`、`session.py`、`permissions.py` | `ruff check .` + `ruff format --check .` + **全量** pytest |
| `webapp/src/`、`webapp/electron/` | 三端 `tsc --noEmit` + `pnpm run build:renderer`（改了构建配置时） |
| RPC 协议、事件格式 | 上项 + `py -3.13 webapp/smoke-ws.py`（期望 `SMOKE_PASS`）+ 全量 pytest |
| 打包脚本、依赖清单 | 上项 + 至少跑一次 `pwsh -File scripts\打包.ps1 -SkipWeb -Pack` 验证产物 |
| 准备发版 | 全量测试 + 前端构建 + 完整打包 + 检查发布包里的 `config.yaml` 是模板 |

### 7.2 常用命令

| 目的 | 命令 |
| :--- | :--- |
| 全量测试 | `py -3.13 -m pytest -q` |
| 单模块测试 | `py -3.13 -m pytest tests/test_permissions.py -q` |
| lint / 自动修复 | `ruff check .` / `ruff check . --fix` |
| 格式检查 / 格式化 | `ruff format --check .` / `ruff format .` |
| 前端类型检查 | `cd webapp && pnpm run check:renderer && pnpm run check:main && pnpm run check:preload` |
| 前端构建 | `cd webapp && pnpm run build` |
| 端到端冒烟 | `py -3.13 webapp/smoke-ws.py`（期望 `SMOKE_PASS`） |
| 打包 | `pwsh -File scripts\打包.ps1`（可选 `-SkipWeb` / `-SkipBackend` / `-Pack`） |

### 7.3 硬规则

- **打包不要并行跑两份**：两个 PyInstaller 进程同时写 `build/` 会破坏缓存，
  需要重跑时先等前一个结束；怀疑缓存坏了用 `-CleanBackend`。
- **不得用 `git commit --no-verify` 跳过钩子**。钩子失败就修代码，不是绕过钩子。
- 测试全量约 2~3 分钟，不要因为"太慢"只跑单个文件就声称全绿。
- 结果如实报告：通过数、跳过数、失败数、耗时。只跑了子集就写明"未跑全量"及风险点。
- 需要真实 API Key 才能验证的功能（真实对话、QQ 渠道联通），标注"需真实环境验证"，
  不要用 mock 通过就说功能可用。

---

## 八、文档同步

| 触发 | 必须更新 |
| :--- | :--- |
| 任何用户可见行为变化 | `CHANGELOG.md`（Keep a Changelog 结构，中文） |
| 新增/变更配置项 | `example_config.yaml` + 根 `README.md` 对应章节 |
| 架构、分层、核心链路变化 | `docs/`（新建或更新设计文档）并在 `docs/README.md` 索引补一行 |
| 提示词模块或注入语义变化 | 根 `README.md`「换成你自己的角色」章节 |
| 测试基线变化（新增/退役用例） | `tests/README.md` 的计数说明 |
| 新增依赖 | `requirements.txt`（运行）或 `requirements-dev.txt`（开发），并在提交信息说明原因 |
| 规范本身变化 | 本文件与 `CONTRIBUTING.md` |

- 易过期数字（测试条数、打包体积、耗时）标注测量日期。
- 文档与代码不一致时以代码为准，同时修正文档；判断不了就先问。
- 文档更新与代码改动放在同一次提交，不要留"稍后补文档"。
- `docs/refactor/` 是历史过程档案，**不要修改其中的行号/用例数**去"对齐现状"。

---

## 九、安全与隐私

本项目在用户本机运行，能读文件、执行命令、连外部模型，因此边界比其他项目更硬。

- **绝不写入、打印、提交真实 API Key**。示例一律用 `your-xxx-api-key` 占位。
  发现 `config.yaml` 或任何文件里出现真实 Key 被 `git add`，立即停止并告知用户。
- **对话内容与记忆是用户隐私**：不得提交 `database/`、`memory/`、`sessions/`、`logs/`；
  不得在日志、汇报、示例里粘贴用户真实对话原文。
- **不新增遥测、统计、埋点、崩溃上报**；不新增需要自建后端的依赖。
- **工具与命令的沙箱限制不得放宽**：`permissions.py` 的策略是安全边界，
  加白名单、提级沙箱、关闭审批都必须由用户明确要求。
- 图片只发往用户自己配置的模型端点，不得发往其它地址。
- 渠道鉴别信息（NapCat `access_token`）只存在用户本地 `config.yaml`，不进代码、不进测试用例。
- 桥接 token 只在主进程流转，**不得进入渲染进程**（`preload` 的类型化 API 不得暴露它）。

---

## 十、提交与推送

### 10.1 触发条件

- 任何一次代码、资源、文档、脚本的改动，**验证通过后即可提交**，不需要用户再提醒。
- 推送前先看 10.2；用户说"先别推""只改不提交"时，以用户指令为准，并在汇报里说明未推送。
- 只读任务（答疑、看代码、查资料）不产生提交，不要为了"有产出"去改文件。
- **工作区存在他人未提交改动时，不要一并提交**（见 3.1）。

### 10.2 推送前自查（任何一条不过就停）

- `git status --short` 里每个文件都是本次预期改动，没有临时脚本、日志、截图、构建产物。
- 没有 `config.yaml`、真实 API Key、`database/`、`sessions/`、`.agent-teams/`。
- 提交信息格式正确：`type(中文scope): 中文简述`，简述不超过 30 字，结尾不加句号。
- 本地验证已按第七章跑过。
- 当前分支就是要推的分支（默认 `main`）；不确定就先问。

### 10.3 标准命令

```bash
git status --short
git add <明确路径>          # 仅当输出里没有意外文件时，才可 git add -A
git commit -m "fix(渠道): 断线重连后重复回复"
git log --oneline -1
git -c http.sslBackend=schannel push origin HEAD   # 本机必需，见 10.4
git status -sb              # 确认无 ahead / behind
```

### 10.4 本机推送通道实测（2026-11-03）

`github.com:443` 的 TCP 是通的，卡在 TLS 握手的证书吊销列表查询。三种写法实测结果：

| 命令 | 结果 |
| :--- | :--- |
| `git ls-remote origin`（默认） | 失败：`schannel: ... CRYPT_E_NO_REVOCATION_CHECK` |
| `git -c http.schannelCheckRevoke=false ...` | 失败（全局 gitconfig 里已有该项也不起作用） |
| `git -c http.sslBackend=schannel ...` | **成功**，可稳定复现 |

所以本机执行远端操作统一用：

```bash
git -c http.sslBackend=schannel push origin HEAD
git -c http.sslBackend=schannel fetch origin
```

代价是不再查询证书吊销状态（证书链本身仍校验）。这是**本机环境**的临时绕过，
根因应在环境侧修（统一 git 版本或补齐 CA），**不要写进 `.git/config` 提交上去**。

另一种报错 `Failed to connect to github.com port 443` 才是真·网络不通，
解法是开代理或配 SSH key，与上面的证书问题不要混为一谈。

### 10.5 禁止事项

- 禁止 `git push --force` / `--force-with-lease`，禁止改写已推送的历史。
- 禁止 `git commit --no-verify`。
- 禁止"验证没过先推上去让 CI 看看"。
- 禁止推送 tag、创建 Release、合并/删除分支，除非用户明确要求。
- 禁止推送密钥、签名材料、`.agent-teams/`、`build/`、`发布包/`、`database/`。
- 推送失败不反复重试、不换方式绕过；失败就停下来说明原因，把提交留在本地。

### 10.6 失败处理

| 现象 | 处理 |
| :--- | :--- |
| `CRYPT_E_NO_REVOCATION_CHECK` | 换 10.4 的 `-c http.sslBackend=schannel`；仍失败则停止并说明 |
| push 被拒（non-fast-forward） | 停止，说明本地与远端差异；经用户确认后再 `pull --rebase`；绝不强推 |
| 认证失败 / 无凭据 | 停止，提示用户在本地完成推送或配置凭据；不要把 token 写进任何文件 |
| 网络不可用 | 停止，说明提交已在本地，网络恢复后再推 |
| 推送后 CI 变红 | 下一轮优先修复；不得把这轮改动当作已完成 |

---

## 十一、提交前自检清单

- [ ] 改动范围与用户要求一致，没有夹带无关修改
- [ ] `ruff check .` 与 `ruff format --check .` 通过
- [ ] 相关测试通过（或明确说明未跑及原因）
- [ ] 改了前端：三端 `tsc --noEmit` 通过
- [ ] 改了 RPC / 事件格式：`webapp/smoke-ws.py` 输出 `SMOKE_PASS`
- [ ] 文档已按第八章同步（`CHANGELOG.md` / `example_config.yaml` / `README.md`）
- [ ] 提交信息符合 `type(中文scope): 中文简述`
- [ ] `git status --short` 无临时文件、无构建产物、无 `config.yaml`、无对话数据
- [ ] 已推送，且 `git status -sb` 无 ahead / behind（或已说明未推送原因）

---

## 十二、汇报格式

固定四段，简短，不写场面话：

1. **改了什么**：文件清单 + 每个文件一句话。
2. **为什么**：一句话说明动机。
3. **验证**：跑了哪些命令、真实结果是什么（通过数 / 失败数 / 耗时）。
4. **未做 / 风险**：没有就写"无"。

---

## 十三、常用命令速查

```bash
# ---- 状态与差异 ----
git status --short && git diff --stat
git log --oneline -5

# ---- 验证 ----
py -3.13 -m pytest -q                      # 全量（约 2~3 分钟）
py -3.13 -m pytest tests/test_bridge.py -q # 单文件
ruff check . && ruff format --check .
cd webapp && pnpm run check:renderer && pnpm run check:main && pnpm run check:preload

# ---- 端到端冒烟 ----
py -3.13 webapp/smoke-ws.py                # 期望 SMOKE_PASS

# ---- 打包 ----
pwsh -File scripts\打包.ps1                # 全量
pwsh -File scripts\打包.ps1 -SkipWeb -Pack # 只改了 Python
pwsh -File scripts\打包.ps1 -CleanBackend  # 依赖变了 / 缓存坏了

# ---- 提交与推送（本机通道见 10.4）----
git add <路径> && git commit -m "fix(scope): 中文简述"
git -c http.sslBackend=schannel push origin HEAD
git status -sb
```
