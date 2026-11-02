# 变更日志

本项目的所有重要变更记录在此。格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

> 说明：v1.5.2 之前的版本是**补记**——依据 git 提交历史整理，粒度较粗。
> 从 v1.5.3 起，每次发版前在 `## Unreleased` 下补条目，发版时整体改名。

---

## [Unreleased]

版本号待定（`README.md` 提到 v1.5.3，但仓库最高 tag 为 v1.5.2，发版前需统一）。

### 新增

- **工程规范基建**：`pyproject.toml`（ruff 规则与 pytest 配置统一入口）、`.editorconfig`、
  `.pre-commit-config.yaml`、`requirements-dev.txt`、`.github/workflows/ci.yml`（lint / test / webapp 三作业）、
  `CONTRIBUTING.md`、`CHANGELOG.md`、`docs/README.md`（文档索引）。
- **`AGENTS.md`**：面向 AI 编码代理的门禁规则（环境陷阱、验证分级、依赖方向红线、
  安全边界、提交推送流程与本机推送通道实测）。
- **`.gitattributes`**：源码统一 LF 入库，PowerShell 脚本保留 CRLF，二进制不做转换。
- `.git-blame-ignore-revs`：为后续纯格式化提交保留 `git blame` 归因。

### 变更

- **代码格式统一**：全量执行 `ruff format`，43 个 Python 文件重排；`ruff check` 由 1279 项降至 0。
- `pytest.ini` 合并进 `pyproject.toml` 的 `[tool.pytest.ini_options]`（配置单一来源，行为不变）。
- 根目录 8 个 PyInstaller `.spec` 归档至 `build-specs/archive/`（打包脚本使用命令行参数，不读取 spec）。
- 清理空目录 `channels/`、`webapp/verify/`，删除 `webapp/reinstall.log`。

### 修复

- **`castorice/memory/` 的 8 个源文件从未入库**：`.gitignore` 的 `memory/` 规则缺少前导斜杠，
  把源码包与运行期数据目录一并忽略了，导致 clone 后项目无法启动。规则改为 `/memory/`，
  同类规则（`/sessions/`、`/database/`、`/logs/`、`/channels/`）一并收紧，文件已补入版本控制。
  由于 ruff 默认尊重 `.gitignore`，该包历史上也从未被 lint / 格式化，本次一并修掉 14 项
  （导入排序、`__all__` 排序、引号注解、三处循环变量覆写、世界书状态筛选的可读性重写）。
- `schedules.json` 与根 `channels/` 未被忽略（运行期数据可能被误提交）。
- `castorice/notify.py`：`Any` 未导入（3 处注解引用未定义名，靠 `from __future__ import annotations` 未暴露）。
- `castorice/tools/fs.py`：`_atomic_write` 写入失败时残留临时文件，改为清理半成品后抛出。
- `castorice/tools/fs.py`：`hashlib.sha1` 显式标注 `usedforsecurity=False`（内容指纹，非安全用途）。
- `ai.py`：`with Image.open(path) as image` 覆写 `with` 绑定变量，改为独立句柄变量。
- `castorice/session.py`：删除从未被读取的 `header_loaded` 变量。
- `castorice/bridge.py` / `castorice/tools/life.py`：逐行解析循环覆写迭代变量，改用独立变量名。
- `ai.py`、`castorice/bridge.py`、`castorice/channels/napcat.py`：异常链补 `raise ... from ...`（5 处）。
- `tests/test_backward_compat.py`：断言不再绑定单引号字面量，改为同时接受单双引号，避免被格式化打断。

---

## [v1.5.2] - 2026-10-19

### 修复

- 打包版启动卡死 240 秒：sidecar 启动阶段的看门狗超时与握手竞态（`e1982a2`）。

---

## [v1.5.1] - 2026-10-18

### 修复

- 添加模型提供商时静默失败（界面无反馈、配置未写入）（`4139adc`）。

---

## [v1.5] - 2026-10-18

第四期：界面与体验重构，修复十项既有缺陷。

### 新增

- 程序与数据分离，新增增量补丁包机制（`c618575`）。
- 工作目录绑定、会话切换与删除、设置入口与引导优化（`044f809`）。

### 修复

- 打包产物 asar 文件映射错误，导致 v1.5 无法正常打包（`74d516e`）。
- 关闭程序后 sidecar 残留为孤儿进程（`575ce83`）。
- 消息气泡左右不对齐、短消息被迫折行（`186a799`）。
- 注入内容泄露给用户；任意目录无法绑定；工具条位置（`01dae35`）。
- NapCat 渠道的启动与关闭装配（`f18f836`）。

---

## [v1.4] - 2026-10-17

第三期：界面从 PySide6 迁移到 Electron 壳 + React Web UI。

### 变更

- Electron 44 + Vite 8 + React 19 + Tailwind 4 壳工程（`webapp/`），
  Python 后端以 sidecar 形态嵌套发布，用户机器无需安装 Python（`5137c5f`）。
- Qt 界面退役，`ui.py` 与 Qt 专属测试同步移除。

### 修复

- 打包白名单断言适配新的 Electron 管线（`7d4f7bf`）。

---

## [v1.3] - 2026-10-17

第二期：渠道、主动聊天与提示词优化。

### 新增

- **QQ 渠道**：NapCat / OneBot 11 反向 WebSocket 适配器，含 fail-fast 校验、
  指数退避重连、`message_id` 幂等去重（`35d9ee0`）。
- **主动聊天**：调度器与桌面 + QQ 双投递，只补发最近一次错过的时点、
  先落盘后投递（`63200fd`）。
- **触发词注入**：提示词模块按命中关键词动态加载，单轮提示词体积下降 55%~68%（`e3b9ab2`）。

---

## 早期版本（v1.3 之前，未打 tag）

按时间倒序，依据提交历史整理，不含版本号与发布日期。

- **内核重构**（2026-10-16，`8e996ab`）：引入 `castorice/` 内核包——四模式注册表
  （聊天 / 工作 / 编程 / 学习）、会话日志、agent 循环、权限审批网关、工具集、
  记忆引擎、渠道接口。同期脚本收进 `scripts/`、文档收进 `docs/`（`68e6225`）。
- **功能扩充**（2026-10-05 前后）：图片发送（自动压缩后交给视觉模型）、
  表情包按情绪自动匹配、深浅色主题切换。
- **项目起点**（2026-07-17）：PySide6 桌面界面、角色扮演提示词体系、
  本地向量记忆、联网搜索。
