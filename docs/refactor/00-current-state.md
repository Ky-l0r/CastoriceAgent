# 00 · CastoriceAgent 现状审计（第一期改造基线）

> 审计对象：`D:\Subject\CastoriceAgent`（git 工作区）。
> 本文件只描述**代码实际做了什么**，每条都给出文件与行号。
> 面向后续任务：t2 的测试基线从这里取「纯逻辑函数清单」，
> t3/t4/t6/t7 从这里取「不可破坏的既有行为」。

## 1. 代码盘点

| 文件 | 行数 | 职责 |
| :--- | ---: | :--- |
| `ai.py` | 2137 | 配置读写、提示词加载、ChromaDB 记忆、LLM 适配、搜索后端、工具注册表、表情包、`AI` 主类 |
| `ui.py` | 3763 | 主题系统、气泡/头像/输入控件、`AIWorker`、`SettingsDialog`、`ChatWindow`、离线 `MockBot` |
| `main.py` | 287 | 环境准备、文件日志、首次引导、启动 `QApplication` |
| `config.yaml` | 63 | `active_provider` / `search` / `emoji` / `providers`（4 家） |
| `example_config.yaml` | 57 | 发布用占位配置 |
| `README.md` | 409 | 用户文档 + 开发者章节 |
| `docs/使用说明.txt` | — | 随发布包分发的说明书 |
| `scripts/打包.ps1` | 89 | PyInstaller 文件夹模式打包 |
| `scripts/requirement_install.py` | — | 依赖安装脚本 |
| `prompts/` | 11 个 `.md`，76,222 字节 | 角色设定 |
| `Image/` | 1 头像 + 1 ico + 16 张表情包 | 资源 |
| `database/` | ChromaDB 持久目录 | 记忆库 |

**测试现状：仓库内没有任何测试文件，没有 pytest 配置，没有 CI。**
（`.vscode/settings.json` 仅编辑器设置。）

## 2. `ai.py` 真实能力

### 2.1 配置层

- `ConfigManager`（`ai.py:161-269`）：`yaml.safe_load` 读盘；`get("a.b.c")` 点号路径取值；
  `get_provider_config(name)`；`get_active_provider()`（未配置则抛错）；
  `provider_names` 属性。
- **注释保持的写回**（关键既有能力）：
  `update_yaml_scalars(text, updates)`（`ai.py:95-158`）手写行级解析，
  `_yaml_key_of` + `_locate_yaml_key` 定位键路径，
  **只替换目标行的值、保留行尾注释**；
  键不存在时插到父块末尾；**父块找不到则跳过该键**。
  `set_values` → 读文本 → 改写 → 写回 → `_load_config()`。
- `set_provider_values` / `set_active_provider` 是 `set_values` 的语法糖。

### 2.2 提示词层

- `PromptManager`（`ai.py:276-323`）：把 `prompts/` 下 `.md`/`.txt` **按文件名排序**
  全量拼接成一条 system prompt。
- **触发词机制不存在**：`prompts/Castorice.md`、`world.md`、`characters.md` 等文件里
  写了「触发词 / 连带触发词」，但代码中**没有任何解析或按需注入逻辑**——
  全量拼接（76KB）意味着这些标注事实上没有生效。
  > 这是 README 第 102 行「按触发词动态注入」表述与实现不符之处，后续应在文档中校正，
  > 或由模式化改造（t3）顺带实现分层注入。

### 2.3 记忆层

- `MemoryManager`（`ai.py:330-458`），ChromaDB `PersistentClient(path="database")`，
  集合名 `memories`。
  - `add_memory(content)`：**id 取 `str(collection.count() + 1)`**（`ai.py:384`），
    metadata 存 `timestamp/date/time/weekday`（时间不入正文，避免干扰向量检索）。
  - `search_memories(query, n=5)`：query → 把 `[timestamp]` 前缀拼回每条结果返回。
  - `clear()` / `collection` / `count`。
- **缺陷**：`count()+1` 作为 id 在「删除后重建集合 → 重号」与「并发写入」下会撞 id；
  t6 升级为三模式记忆时改为 `uuid4` 并保留旧数据可读。

### 2.4 模型适配层

- `LLMProvider` 抽象基类（`ai.py:465-498`），
  `chat_completion(messages, stream, **kwargs) -> (text, tool_calls)`。
- **唯一实现** `OpenAICompatibleProvider`（`ai.py:501-610`）：
  任何 OpenAI 兼容端点都能用；支持**流式文本 + 流式工具调用增量**（按 `tc.index` 归槽累积
  id/name/arguments）。
- `LLMProviderFactory.create_provider`（`ai.py:613-634`）**无分支**——
  无论 provider 名是什么都返回 `OpenAICompatibleProvider`。
  **没有厂商分级适配、没有能力表（能力靠猜）、没有重试、没有超时参数。**
- `AI.supports_vision`（`ai.py:1577-1600`）：优先读 provider 的 `vision` 字段，
  否则用正则 `VISION_MODEL_RE`（`ai.py:1567-1572`）匹配模型名。
  这是**当前唯一的「模型能力」概念**。

### 2.5 工具层

- `Tool` dataclass（`ai.py:641-655`）：`name / description / parameters(JSON Schema) / function`。
- `ToolRegistry`（`ai.py:658-702`）：`register` / `to_openai_specs` / `call`。
  `call` 异常时返回 `"错误: 调用工具 X 失败: …"` 字符串；未知工具也返回字符串。
- `create_builtin_tools(search_config)`（`ai.py:1063-1093`）**只注册两个工具**：
  `get_current_time`、`web_search`。
- **搜索后端有 6 种**（`ai.py:737-1060`）：`BingHTMLSearch`、`DDGHTMLSearch`、
  `TavilySearch`、`SerperSearch`、`BingAPISearch`、`BochaSearch`；
  `SearchManager` 按 `provider` 选择，`auto` 时 key 型优先、HTML 型兜底，
  **失败自动降级**；可选抓取前 N 条网页正文（`_fetch_content`，8s 超时，
  `_html_to_text` 截断 600 字）。
- **没有的工具**：文件读写/编辑、命令执行、目录列举、路径搜索、生成文档、
  生活服务、音乐播放、MCP、子代理、待办、计划模式。**工具生态基本为零。**

### 2.6 表情包层

- `EmojiCatalog`（`ai.py:1182-1336`）：扫描 `Image/表情包`（不存在则回退 `Image`，
  跳过 `_` 开头文件），文件名主干即标签名；
  `DEFAULT_EMOJI_MOODS`（16 项心情描述）、`DEFAULT_EMOJI_KEYWORDS`（16 项关键词），
  可被 `config.yaml` 的 `emoji.moods/keywords` 覆盖；
  `match_by_mood(text)` 按「关键词长度 ≥ 2 的命中数」打分取最高，
  全不命中时从 `_FALLBACK_MOOD_ORDER`（6 个温和情绪）随机兜底。
- `EmojiSession`（`ai.py:1338-1399`）：`enabled / probability / cooldown_turns /
  max_per_session` 的回合与冷却判定。
- `EMOJI_TAG_RE`（`ai.py:1101`）解析 `[表情:名称]`；`_EMOJI_OPT_OUT_RE`（`ai.py:1107`）
  识别「不要表情」类请求。**当前设计是「模型不写标记，由程序在回复末尾自动挑一张」**
  （`EMOJI_INSTRUCTIONS`，`ai.py:1508-1520`）。

### 2.7 `AI` 主类与 agent 循环

- `AI.__init__`（`ai.py:1437-1478`）：ConfigManager → PromptManager → MemoryManager →
  provider → `create_builtin_tools` → `_init_emoji` → 重置 messages。
- `_build_system_prompt`（`ai.py:1553-1559`）= 基础提示词 + 表情包说明 +
  `TOOL_INSTRUCTIONS`（`ai.py:1484-1493`，**硬编码列出两个工具名**）+
  `TIME_INSTRUCTIONS`（`ai.py:1496-1503`）。
- `get_response_stream`（`ai.py:1755-1883`）——**这是全项目的核心，也是红线**：
  1. `emoji_session.mark_turn()`；
  2. 检索记忆（只发图片时不检索）；
  3. 组装 user 消息 = `【当前时间】` + `【相关记忆】` + 正文（**记忆放 user 侧省 token**）；
  4. `tool_specs = self.tool_registry.to_openai_specs()`（**无条件**）；
  5. `for _ in range(MAX_ITERATIONS=8)`：`chat_completion(..., tools=tool_specs)` →
     `yield ('text', …)` → 无 tool_calls 则 break → 否则追加 assistant/tool_calls 消息 →
     逐个 `yield ('tool', {'name', 'status':'start'})` → 执行 →
     `yield ('tool', {… 'status':'done'})` → 追加 `role: tool` 消息；
  6. `for…else`：触顶时追加「（已达到处理步数上限，以上是当前进展。）」；
  7. 挑表情包 → `yield ('emoji', …)`；
  8. 历史存纯文本回复，记忆存 `"用户: …\n助理: …"`；
  9. `yield ('done', full_reply)`；异常路径 `yield ('text', 错误)` + `('done', 错误)`。
- 兼容接口：`get_ai()`（单例）、`get_ai_response()`、`AIFactory`、
  `change_provider(keep_history=)`、`clear_history`、`reload_emojis`、`clear_memories`、
  `get_history`、`get_memory_count`、`messages`、`provider_name`、`supports_vision`、
  `encode_image_data_url`（Pillow 压缩到最长边 1280、转 JPEG 质量 88）。

## 3. `ui.py` 真实能力

- 主题系统：`ThemeMode`（system/light/dark）、`ThemeSettings`（存 `ui_settings.json`）、
  `Colors` 元类按模式解析色值、`create_palette()`。
- 控件：`AvatarLabel`（圆形裁剪）、`WrapLabel`（手工换行）、`ChatBubble`（气泡 + 内嵌表情图）、
  `TitleBar`/`TitleBarButton`（无边框自绘）、`InputIconButton`（QPainter 自绘图标）、
  `EmojiPicker`（QMenu）、`AttachmentItem`、`RowEntranceAnimator`（入场动画）。
- `AIWorker(QThread)`（`ui.py:815-895`）：4 个 Signal
  （`chunk_received`/`tool_event`/`emoji_event`/`finished`）+ `error`；
  `run()` 兼容「元组事件流」与「纯字符串旧式流」；`stop()` 只置 `_is_running=False`。
- `SettingsDialog(QWidget)`（`ui.py:1767-2252`）：外观（3 个主题单选）+ 提供商表单
  （API Key 显隐 / Base URL / 模型 / Temperature / vision 勾选）；
  `_on_save`（`ui.py:2215-2252`）→ `config_manager.set_values({("active_provider",):…,
  ("providers", p, k):…})` → `bot.change_provider(provider, reload_config=True,
  keep_history=True)`。
- `ChatWindow(QMainWindow)`（`ui.py:2302-3645`）：
  - **打字机**：`_typewriter_chars` 队列 + `QTimer` 每 tick 吐一个字符（`ui.py:3393-3404`）；
  - **表情包流式解析**：`_feed_text`（`ui.py:3179-3249`）用 `_emoji_parse_buffer` +
    `_is_tag_prefix`（`ui.py:3251-3273`）处理被切碎的 `[表情:…]` 标记，
    `EMOJI_BUFFER_LIMIT=40` 兜底；
  - **工具气泡**：`_on_tool_event`（`ui.py:3353-3374`）在 `start` 时插入
    「🔧 正在调用 X …」QLabel，非 start 时移除；
  - **线程安全**：`_send_message` 拒绝并发（`ui.py:2949-2950`），
    `_release_worker`/`_orphan_workers` 防止「Destroyed while thread is still running」
    直接崩进程（`ui.py:2907-2942`）；
  - **图片附件**：`_pending_images` + `AttachmentItem`；
  - 离线演示：`MockBot` + `MockConfigManager`（`ui.py:3676-3739`），
    `python ui.py` 可直接看界面。

## 4. `main.py` 真实能力

- `app_root()`（定义在 `ui.py:41`）解析程序目录；`setup_environment` 建
  `database/prompts/Image` 目录、缺 `config.yaml` 时从 `example_config.yaml` 复制；
- `setup_file_logging` → `logs/app.log`；
- `is_placeholder_key` 识别 `your-*`/`sk-xxx` 占位 Key；
  `guide_first_run` 在占位时自动打开设置窗口；
- `show_startup_error` 用 `QMessageBox` 显示启动失败原因；
- `main(root)` 用**绝对路径**构造 `AI(...)`（打包后仍能定位资源）。

## 5. 能力矩阵：已具备 vs 缺失

图例：✅ 具备且可用 ｜ ⚠️ 部分具备或有缺陷 ｜ ❌ 完全缺失

| 能力 | 状态 | 证据 |
| :--- | :--- | :--- |
| 多提供商配置（4 家） | ✅ | `config.yaml:36-64`，`ConfigManager.get_provider_config` |
| 切换提供商不重启 | ✅ | `AI.change_provider`（`ai.py:1901-1923`）+ SettingsDialog |
| 配置写回保留注释 | ✅ | `update_yaml_scalars`（`ai.py:95-158`） |
| 流式输出 + 打字机 | ✅ | `get_response_stream` + `_typewriter_tick` |
| 工具调用（function calling） | ✅ | 流式增量累积（`ai.py:551-591`） |
| 联网搜索（6 后端降级） | ✅ | `SearchManager`（`ai.py:942-1030`） |
| 网页正文抓取 | ✅ | `_fetch_content`（`ai.py:1020-1030`） |
| 向量记忆 + 时间前缀 | ✅ | `MemoryManager`（`ai.py:330-458`） |
| 表情包情绪匹配 | ✅ | `EmojiCatalog.match_by_mood`（`ai.py:1313-1336`） |
| 视觉输入（图片） | ✅ | `supports_vision` + `encode_image_data_url` |
| 深浅主题、跟随系统 | ✅ | `ThemeSettings`/`Colors` |
| 离线界面演示 | ✅ | `MockBot`（`ui.py:3696-3739`） |
| 一键打包 | ✅ | `scripts/打包.ps1` |
| **模式化（Chat/Work/Code/Learn）** | ❌ | 无任何模式概念 |
| **按模式的工具可见性** | ❌ | `to_openai_specs()` 无条件全量（`ai.py:670-682`、`1806`） |
| **权限审批网关** | ❌ | 无；工具直接执行 |
| **沙箱/目录边界** | ❌ | 无；工具无路径参数 |
| **文件读写 / 命令执行工具** | ❌ | 只有 2 个内置工具（`ai.py:1069-1091`） |
| **会话持久化 / 恢复** | ❌ | `_messages` 纯内存（`ai.py:1472`） |
| **终止原因显式化** | ❌ | `for…else` 隐式判断（`ai.py:1850-1854`） |
| **检查点 / fail-closed** | ❌ | 无 |
| **记忆分层（wiki/summary/embedding）** | ❌ | 单一 embedding 集合 |
| **Worldbook / 条目生命周期** | ❌ | 无 |
| **提示词分层注入（触发词）** | ⚠️ | 文件里标注了触发词，**代码不解析**，全量拼接 |
| **模型能力分级** | ⚠️ | 只有 `vision` 一个能力，靠正则猜模型名 |
| **多厂商差异化适配** | ⚠️ | `LLMProviderFactory` 无分支，全部走 OpenAI 兼容 |
| **重试 / 超时 / 限流** | ❌ | 无 |
| **工具结果大小上限 / spill** | ❌ | 无；搜索正文最多 600 字硬截断 |
| **工具并行 / 独占调度** | ❌ | 顺序执行 |
| **MCP 扩展** | ❌ | 无 |
| **多平台接入（飞书/微信/QQ）** | ❌ | 无，仅本地 Qt 窗口 |
| **主动聊天 / 定时任务** | ❌ | 完全无 |
| **测试 / CI** | ❌ | 无测试文件、无 pytest 配置、无 CI |

## 6. 既有行为中「不得回归」的清单（后续任务的回归基线）

1. **事件协议**：`('text'|'tool'|'emoji'|'done')` 元组，`ai.py:1755-1883` 产出、
   `ui.py:848-895` 消费。详见 `04-dsh-harness-notes.md` 第 5.1 节。
2. **配置写回**：`update_yaml_scalars` 的注释与排版保持。详见 5.2 节。
3. **表情包**：`match_by_mood` 的关键词打分 + 兜底顺序；
   `EmojiSession` 的概率/冷却/上限判定；流式标记解析（`_feed_text`/`_is_tag_prefix`）。
4. **视觉**：`supports_vision` 的「配置优先、正则兜底」判定顺序；
   `encode_image_data_url` 的 1280 上限与 JPEG 质量。
5. **搜索**：6 后端名与 `auto` 的优先级（key 型优先、HTML 型兜底）、失败降级、
   `fetch_top_n` 抓取。
6. **界面**：主题即时生效与持久化、无边框窗口拖拽、打字机、入场动画、
   `AIWorker` 的线程安全防护（`_release_worker`/`_orphan_workers`）。
7. **打包**：`python main.py` 能起；`scripts/打包.ps1` 无需改动即可收集新模块。

## 7. 附录：`prompts/` 清单

| 文件 | 字节 | 内容 |
| :--- | ---: | :--- |
| `system.md` | 3,466 | 遐蝶系统规则（回复长度、即时聊天表达、禁止旁白等）；第 3 行提到 `tools_system.md`，**该文件不存在** |
| `identity.md` | 1,001 | 角色定位与工作边界 |
| `soul.md` | 10,987 | 人格核心 |
| `tone-rules.md` | 3,155 | 语气规则 |
| `talk_system.md` | 1,698 | 对话系统规则 |
| `world.md` | 11,194 | 世界观 |
| `story.md` | 3,806 | 经历 |
| `characters.md` | 33,336 | 人物关系（含触发词标注） |
| `Castorice.md` | 3,920 | 本体条目（含触发词标注） |
| `canon_quotes.md` | 2,027 | 原作台词 |
| `appellation.md` | 1,632 | 称谓映射 |

拼接顺序 = **文件名 `sorted()` 升序**（`PromptManager._load_prompts`，`ai.py:299`）：
`Castorice.md → appellation.md → canon_quotes.md → characters.md → identity.md →
soul.md → story.md → system.md → talk_system.md → tone-rules.md → world.md`。
合计 76,222 字节 ≈ 约 2.5 万 token，**每轮请求都全量重发**。
