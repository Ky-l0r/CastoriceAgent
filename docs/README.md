# docs/ 文档索引

面向不同读者的入口。**用户向文档在仓库根的 `README.md`**，这里放开发与设计资料。

## 目录

| 路径 | 内容 | 读者 |
| :--- | :--- | :--- |
| [`使用说明.txt`](使用说明.txt) | 随发布包分发的最终用户说明书 | 使用者 |
| [`screenshot-dark.png`](screenshot-dark.png) / [`screenshot-light.png`](screenshot-light.png) | 界面截图（README 引用） | — |
| [`refactor/`](refactor/) | 四期改造的过程档案：现状审计、差距矩阵、目标架构、验证报告、渠道契约、调度器设计 | 维护者 |

## refactor/ 说明

`refactor/` 是**历史过程文档**，记录每一期改造「为什么这么做」，其中的文件行号、
用例数、模块状态都对应当时的代码快照，**不代表当前实现**。
需要了解当前行为，以代码、`tests/` 与根 `README.md` 为准。

| 文档 | 记录内容 |
| :--- | :--- |
| `00-current-state.md` | 第一期改造前的现状审计（含文件与行号） |
| `01-gap-matrix.md` | 九项目标能力的差距矩阵与第一期落点 |
| `02-architecture.md` | 目标架构与迁移路径（`ai.py` 保留为客户门面） |
| `03-verification-report.md` | 第一期独立验证报告 |
| `04-dsh-harness-notes.md` | DeepSeek Harness 范式抽取与 Python 侧落点 |
| `05-channel-contract.md` | 渠层接口约定与 `sessions/` 落盘约定 |
| `06-scheduler.md` | 主动聊天调度器与双投递设计 |
| `07-phase4-ui.md` | 第四期界面重构（v1.5）与十项缺陷修复记录 |

## 新增文档的约定

- 面向**使用者**的内容写进根 `README.md`，不要放这里；
- 面向**维护者**的设计/决策文档放 `docs/`，文件名用 `kebab-case.md`；
- 已完成的阶段性过程文档归入 `docs/refactor/`，并在上表补一行；
- 文档里引用代码位置时写明**文件名 + 函数名**，行号容易失效。
