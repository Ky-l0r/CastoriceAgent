# 贡献指南

面向要改这个仓库的人。**只是使用的话看 `README.md` 就够了。**

> 使用 AI 编码代理（Claude Code / Codex / Cursor / Copilot 等）时，
> 请让它先读 [AGENTS.md](AGENTS.md)——那里是给代理的门禁规则，
> 比本文件更严格（验证分级、提交推送流程、不可越过的安全边界）。

---

## 环境准备

| 组件 | 版本 | 用途 |
| :--- | :--- | :--- |
| Python | 3.9+（开发用 3.13） | 后端内核与 sidecar |
| Node.js | 24.x | Electron 壳 + Web UI |
| pnpm | 9.x | 前端依赖管理（不要用 npm / yarn，lockfile 是 pnpm） |

```bash
# 后端
pip install -r requirements.txt -r requirements-dev.txt

# 前端（npmjs 直连慢时用镜像）
cd webapp
pnpm install --registry=https://registry.npmmirror.com
```

---

## 改代码前的三步

```bash
# 1. 装提交钩子（只需一次，之后每次 commit 自动跑 ruff）
pre-commit install

# 2. 确认基线是绿的
python -m pytest -q          # 期望 798 passed, 1 skipped
ruff check .
ruff format --check .

# 3. 改，然后重复第 2 步
```

> 测试全量约 2~3 分钟。只改某个模块时可先跑单个文件：
> `python -m pytest tests/test_permissions.py -q`

---

## 代码规范

### Python

- **规则配置只有一处**：`pyproject.toml` 的 `[tool.ruff]`。不要在别处加 flake8 / pylint 配置。
- 提交前 `ruff check .` 与 `ruff format .` 必须是干净的——pre-commit 会自动做，被拦下说明有未格式化改动。
- **行长 100**，双引号，LF 换行，4 空格缩进。
- 类型注解用 `typing` 风格（`Optional[X]` / `List[X]`），不用 `X | None`：
  项目最低支持 Python 3.9，运行期求值会失败。
- 注释与 docstring 用中文，与现有代码保持一致。全角标点不会触发 lint（`RUF001-003` 已关）。

### 前端

- TypeScript 严格模式，三个 tsconfig 分别对应渲染进程 / 主进程 / preload，改完都要过：
  ```bash
  cd webapp
  pnpm run check:renderer && pnpm run check:main && pnpm run check:preload
  ```
- 样式用 Tailwind 工具类 + `src/styles/global.css` 里的 CSS 变量，不要引入新的样式方案。
- 新增组件放 `src/components/`，设置页各分类放 `src/components/settings/`。

### 目录归属

| 想加的东西 | 放哪里 |
| :--- | :--- |
| 内核能力（工具、记忆、权限、渠道） | `castorice/` 对应子包 |
| 桥接 RPC（前端 ↔ 后端） | `castorice/bridge.py` |
| 界面 | `webapp/src/` |
| 角色设定文本 | `prompts/` |
| 测试 | `tests/test_<模块>.py` |
| 构建脚本 | `scripts/`（PowerShell） |
| 设计文档 | `docs/`（用户文档才放根 `README.md`） |

---

## 提交信息

沿用现有的 **Conventional Commits + 中文摘要** 风格，作用是让 `CHANGELOG` 能自动归类：

```
<type>(<scope>): <中文摘要，不超过 30 字>

<可选正文：为什么这么改、边界条件、验证方式>
```

| type | 含义 |
| :--- | :--- |
| `feat` | 新功能 |
| `fix` | 修缺陷 |
| `refactor` | 重构，行为不变 |
| `perf` | 性能 |
| `docs` | 文档 |
| `test` | 测试 |
| `build` / `chore` | 依赖、构建、杂务 |
| `style` | 纯格式（不含逻辑改动，应配合 `.git-blame-ignore-revs`） |

`scope` 用中文短词，对齐现有历史：`启动` `配置` `打包` `界面` `内核` `渠道` `记忆` `权限`。

示例：

```
fix(渠道): NapCat 断线重连后重复回复同一条消息

原因是 message_id 去重表在重连时被清空；改为按 session 生命周期保存，
并为去重表加容量上限（1000 条 FIFO）。
```

---

## 测试要求

- **改内核必带测试**：`castorice/` 下的行为改动需要在 `tests/` 有对应用例，
  且不得删除或跳过既有用例来"让测试变绿"。
- 测试默认**禁止联网**：需要真实网络的用例加 `@pytest.mark.allow_network`。
- 测试不得写入仓库内的固定路径，用 `tmp_path` / `monkeypatch` 隔离。
- 界面行为由 `webapp/smoke-ws.py` 端到端冒烟覆盖，改桥接协议后要跑一次：
  ```bash
  py -3.13 webapp/smoke-ws.py     # 期望输出 SMOKE_PASS
  ```

---

## 技术债登记

以下规则已在 `pyproject.toml` 中**显式豁免**，是已知债务而非"没问题"。
新代码不应扩大这些用法；修改相关模块时顺手收敛。

| 规则 | 现状 | 收敛方向 |
| :--- | :--- | :--- |
| `BLE001` | 90 处裸 `except Exception` | 逐个改为捕获具体异常；确需兜底处加 `# noqa: BLE001` 并写明降级语义 |
| `S110` | 16 处 `try/except/pass` | 至少记一条 debug 日志 |
| `PLC0415` | 20 处函数内导入 | 其中多数是规避循环依赖，保留但需注释原因 |
| `PLR0911/0912/0913/0915` | 少量超长函数 | 拆分时优先抽纯函数到 `castorice/` 子模块 |
| `UP006/UP045` | `typing.List` / `Optional` 写法 | 待放弃 Python 3.9 后一次性迁移 |

`ai.py`（约 3900 行，其中 `AI` 类占一半）是最大的结构性债务：
新增能力请落到 `castorice/` 子包，`ai.py` 只保留对外门面与被内核反向依赖的部分。

---

## 打包与发布

```powershell
pwsh -File scripts\打包.ps1              # 全量打包
pwsh -File scripts\打包.ps1 -SkipWeb     # 只改了 Python
pwsh -File scripts\打包补丁.ps1          # 增量补丁包
```

- 产物在 `发布包\CastoriceAgent\`，**不要提交**（已在 `.gitignore`）。
- 发布前必须确认 `发布包\...\config.yaml` 是示例配置，**不含任何 API Key**。
- `build-specs/archive/` 里的 `.spec` 不参与构建，仅历史存档（见该目录 README）。
