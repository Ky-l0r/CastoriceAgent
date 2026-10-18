# 第四期：界面与体验重构

对应发布版本 **v1.5**。本期不含新功能域，全部工作是**修既有缺陷 + 重做界面观感**
（用户在第三期交付后提出 10 条具体问题，见 §1）。

## 1. 问题清单与根因

| # | 用户描述 | 根因（代码位置） | 处理 |
| :-- | :--- | :--- | :--- |
| 1 | 输入框提示是「给昔涟发消息」 | `Composer.tsx` 占位文案是从昔涟参考实现抄袭时留下的 | 改为「给遐蝶发消息」；全仓清理残留「昔涟」字样 |
| 2 | 风格 / 模型框点击无效 | 两处是纯 `<span>`，从未绑事件（只有推理等级接了 RPC） | 四个状态项全部接入弹层；风格落 `ui_settings.style` 并注入提示词 |
| 3 | 四大工作区没有区别 | 后端模式差异真实存在（工具 0/19/16/8），但前端切换只改一个变量 | 新增 `ModeWorkspace` 右侧面板：工作区 / 代码区 / 笔记本 |
| 4 | 表情包不显示、用户不能发 | ① `localImageUrl` 拼绝对路径，与 `base:"./"` 冲突，打包版必 404；② 无发送入口 | `lib/assets.ts` 统一走 BASE_URL；新增贴纸面板；后端识别用户标记 |
| 5 | 输入框上方有一块空白 | `items-end` 让 36px 按钮与单行 textarea 底对齐，文字被压低 | 改为工具行与发送按钮同排、textarea 独占一行 |
| 6 | 没有最大化、缩放不对 | `TitleBar` 只有 minimize/close，IPC 也没有 maximize 通道 | 主进程加 toggle/is-maximized + 状态广播；渲染层加按钮 |
| 7 | 模型 API 页太复杂 | 每个供应商 5 个字段全平铺，无预设无新增入口 | 改为预设卡片：选厂商自动填地址与模型，高级项折叠 |
| 8 | 用户档案要手填时区 | 六个字段全是文本框，时区要求 IANA 名 | 性别选项 / 生日日期选择器 / 城市下拉搜索自动推时区 |
| 9 | 模式切换那一行不好看 | 第一期 PySide6 的「紫色通栏 + 白胶囊」遗留设计 | 换成滑动指示器的分段选择器（对齐昔涟 SegmentedControl） |
| 10 | 整体不好看 | 样式散落魔法数字、重色块、缺乏层次 | 建立设计令牌（语义色 / 标尺 / 外观变量），重做全局样式 |

## 2. 设计令牌（`webapp/src/styles/global.css`）

三层结构，新组件一律引用，不再写魔法数字：

1. **语义色 `--c-*`**：窗口 / 表面 / 输入 / 气泡 / 导航 / 审批 等，深浅两套；
2. **标尺 `--r-* / --s-* / --fs-*`**：圆角 / 间距 / 字号比例尺；
3. **外观可变项 `--app-*`**：气泡圆角 / 行距 / 段间距 / 气泡开关，设置中心可调。

```
--app-bubble-radius   ← 外观页「圆角半径」滑块
--app-line-height     ← 「行间距」滑块
--app-section-gap     ← 「段间距」滑块
--app-bubbles         ← 「消息气泡」开关（.msg-bubble 走 html[data-bubbles=off]）
```

**注意**：外观变量由 `App.tsx` 统一挂到 `documentElement`，不要在各个面板里
自己 setProperty——那样离开设置页就失效了（本期修正的既有缺陷）。

## 3. 静态资源路径契约

`vite.config.ts` 设了 `base: "./"`，生产环境 Electron 用 `file://` 加载
`dist/renderer/index.html`。**任何写死的 `"/castorice-assets/..."` 绝对路径在
打包版都会解析到磁盘根目录而 404**（dev 下因为有 http server 反而看不出来）。

统一走 `webapp/src/lib/assets.ts`：

| 函数 | 用途 |
| :--- | :--- |
| `assetUrl(path)` | `BASE_URL` + 相对路径，dev 与 file:// 都正确 |
| `AVATAR_URL` | 角色头像 |
| `WELCOME_CHIBI_URL` | 欢迎页 Q 版贴图 |
| `stickerUrl(fileName)` | 表情包：只取文件名拼内置贴纸目录 |

`stickerUrl` 只取 `basename`：后端 emoji 事件的 `path` 是**宿主机绝对路径**
（`D:\...\Image\表情包\害羞.png`），界面不该依赖宿主机目录结构，只借文件名
去拼打包内置的 `public/castorice-assets/表情包/`。

**Q 版贴图混合模式**：素材是白底不透明 PNG。浅色主题用 `multiply`（白色相乘
= 背景透出）；深色主题需要 `invert(1) hue-rotate(180deg)` + `screen`，否则
深色线条糊进深背景。不能用 Tailwind 的 `dark:` 变体——它走
`prefers-color-scheme`，与本项目的 `data-theme` 属性切换不同步。

## 4. 表情包双向链路

### 4.1 AI 发贴纸（既有能力，本期修显示）

`ai.py` 的 `EmojiCatalog` 扫 `Image/表情包`，回复时以 `('emoji', {name, path})`
事件发出。前端 `ChatMessageList` 的 `EmojiEntry` 取 `basename` 拼 URL。

### 4.2 用户发贴纸（本期新增）

界面贴纸面板 → 正文插入 `[表情:害羞]` 标记 → 后端 `_extract_user_stickers`：

- **有视觉能力**（`supports_vision()`）：把贴纸文件当图片一起发给模型，
  模型能真正看到；标记本身从正文去掉（否则模型会复读方括号字符串）；
- **无视觉能力**：标记替换成自然语言描述「（对方发来了表情：害羞）」；
- **未知贴纸名**：不产生图片路径（避免拿不存在的路径去 base64 编码报错），
  只保留可读描述。

## 5. 模式差异化的落点

后端模式差异一直存在（注册表里四个 `Mode` 的可见工具数与沙箱策略不同），
第三期的问题是**前端完全不可见**。本期：

- `ModeTabs` 换成图标分段选择器，四个模式各有 tooltip 说明能力；
- `ModeWorkspace` 在 work / code / learn 时右侧展开，分别显示
  **工作区 / 代码区 / 笔记本**，内容为工作目录、沙箱级别、本轮工具调用；
- chat 模式不显示右侧面板（保持干净的聊天观感）；
- 状态条「模式 · 工作」实时反映，用户能确认切换生效。

### 已知坑：面板刷新回滚模式（本期修复）

`ModeWorkspace` 挂载时拉 `mode.list` 刷新能力信息，原先会连带把后端返回的
`active` 写回 store——而它正好在用户点击模式的同一帧发生，导致**点击后模式
被静默回滚**（表现为「点了没反应」）。修复方式：`setModes(modes, active?)`
的 `active` 改为可选，省略时不覆盖当前 mode。

## 6. 新增 RPC

| 方法 | 参数 | 返回 | 说明 |
| :--- | :--- | :--- | :--- |
| `sessions.list` | `{mode?}` | `{sessions: [...], active}` | 侧栏「最近对话」数据源 |

实现要点：
- 只读会话文件，**不打开 `SessionStore`**（打开会触发单写者锁，把正在用的
  会话锁死）；
- 标题取 JSONL 第一条 `user` 事件的正文前缀（顺序读首行即可，不全量解析）；
- 读失败 / 脏行一律跳过，列表永远可显示。

## 7. 验证记录

| 项 | 结果 |
| :--- | :--- |
| 渲染层 / 主进程 / preload 类型检查 | 全部通过 |
| `pnpm run build` | 通过（产物 470 kB JS / 31 kB CSS） |
| 资源路径断言 | 产物中 `"/castorice-assets` 绝对路径出现 **0** 次 |
| 贴纸随包 | `dist/renderer/castorice-assets/表情包/` 16 张齐全 |
| Python 测试套件 | 788 passed / 1 skipped（与 v1.4 基线一致，无回归） |
| 端到端探针 | `sessions.list` 标题解析、表情包双向、风格注入全 PASS |
| 实机截图 | 深浅两主题 × 欢迎页 / 聊天流 / 三个模式面板 / 六个设置分页 |

### 修掉的实机缺陷（截图阶段发现，非静态检查能覆盖）

1. **React error #310 白屏**：`AppearancePanel` 的新 `useEffect` 被放在
   `if (!settings) return` 之后，首帧与后续帧 hook 数量不一致。已移到提前
   return 之前，并复核其余面板无同类问题。
2. **模式点击无效**：见 §5「已知坑」。
3. **贴图白底**：`screen` 混合模式对白色无效，深色主题留白方块。见 §3。

## 8. 追加项（用户第二轮反馈）

### 8.1 欢迎页：删掉 Q 版贴图

贴图素材是白底不透明 PNG，靠混合模式融入背景，但**白色在深色主题下
无论 multiply 还是 screen 都会留下可察觉的方块**（screen 白色=白，
multiply 白色=背景，但贴图自身的浅色区域仍会发灰）。用户判定为突兀，
直接移除；头像从 76px 放大到 104px 作为视觉焦点。

`.chibi-art` 相关 CSS 保留但已无引用点，后续若要恢复贴图可直接复用
（深色主题下需要 `invert(1) hue-rotate(180deg)` + `screen`）。

### 8.2 工作目录绑定（不用再进设置页）

原先只能改 `config.yaml`，界面上没有任何入口。现在：

- **入口**：右侧工作区面板「工作目录」区块 → 「选择目录 / 更换」按钮；
- **对话框**：`dialog:pick-directory` IPC（renderer 无 fs 权限，必须走主进程）；
- **RPC**：`workdir.bind {mode, path}`，path 为空串表示解绑；
- **安全**：路径必须落在可信根（默认项目目录）内，越界由
  `ModeRegistry.bind_workdir` 抛结构化 `WorkdirError` 拒绝，界面显示原因；
- **即时反馈**：绑定结果写进 store.workdirs，面板立刻显示新路径。

chat 模式不可绑定（`WorkdirPolicy.bindable=False`），UI 按 `bindable`
字段隐藏入口。

### 8.3 设置入口

侧栏底部新增 6 个快捷图标（外观 / 模型 API / 用户档案 / 权限 / 记忆 / 渠道），
点击**直达对应分页**，不用先进设置页再找分类。`SettingsPage` 新增
`initialTab` 参数；用 `useEffect` 跟随变化，否则在设置页内点另一个
快捷入口不会切换分页（组件不卸载）。

### 8.4 会话切换与删除

| RPC | 说明 |
| :--- | :--- |
| `sessions.history {session_id}` | 读 JSONL 投影出 user/assistant/tool/emoji 事件 |
| `session.new` | 关闭旧会话（**空会话也落盘**）、开新文件 |
| `session.switch {session_id}` | flush + close 旧会话 → 打开目标 → 恢复历史进内核 |
| `session.delete {session_id}` | 删文件；**拒绝删正在使用的会话** |

**踩到的坑**：`create_session` 最初只在 `dirty` 时 flush 旧会话，
结果一个刚创建、还没发过消息的会话**根本没有文件**——它既不出现在
`sessions.list` 里（扫盘扫不到），也无法 `session.switch` 过去（文件不存在）。
修复：不 dirty 时也写一次空快照，让空会话作为一个合法的历史条目存在。

删除当前会话被拒绝而不是静默允许：它持有 `_ACTIVE_WRITERS` 单写者登记，
删掉文件会让内核内存状态与磁盘不一致。

### 8.5 引导优化

新增 `GuidanceCard`（info / tip / warn 三种语气）与 `EmptyState` 组件：

- **模型 API**：三步引导（选厂商 → 粘 Key → 保存试一句），空状态说明
  「没有模型就无法对话，配置只存本地」；
- **用户档案**：把原先的小字提示改成引导卡，实时显示「遐蝶会这样称呼你：X」，
  并说明留空不影响使用；
- 其余面板的空状态统一走 `EmptyState`，避免出现无解释的空白区。


## 9. 第三轮反馈（展示隔离 / 任意目录 / 工具条位置）

### 9.1 注入内容不再泄露给用户

**症状**：历史记录里出现了 `【当前时间】… 【相关记忆】…` 这些本该只给模型看的
内容，被当作「用户说过的话」显示在气泡里。

**根因**：`ai.py` 把时间前缀 + 记忆前缀拼进 user 消息后交给内核，内核
`_prepare_history` 又把**整段**写进事件日志的 `content`；界面读 history 时
直接渲染 `content`。

**修复**：内核 `run_stream` 新增可选参数 `display_text`（向后兼容，省略时
行为不变）。事件日志同时记两个字段：

| 字段 | 用途 |
| :--- | :--- |
| `content` | 模型可见的完整文本（含时间/记忆前缀），`derive_messages` 依赖它重建上下文 |
| `display` | 界面展示的用户原文 |

`sessions.history` 优先读 `display`，旧日志无该字段时回落 `content`（历史遗留
数据仍会带前缀，属于无法追溯修正的既往记录）。

**边界纪律**：`display_text` 只影响展示，绝不改变发往模型的内容——否则
模型就会丢掉时间与记忆上下文。

### 9.2 任意位置文件夹可编辑

**原状**：`bind_workdir` 校验路径必须落在「可信根」（默认只有项目目录）内，
用户选 `D:下载我的项目` 会被拒绝。

**修复**：`bind_workdir` 新增 `grant` 参数（默认 False）：

- `grant=False`：保持原样，越界拒绝。**配置文件、程序化调用必须用这个**，
  否则一段配置文本就能把整个磁盘变成可写区；
- `grant=True`：仅当用户在**系统目录对话框里亲自选中**该路径时由界面传入。
  此时跳过校验，并把该目录**追加**为新的可信根（不是替换，已绑定的其它
  目录不受影响）。

授权链路：`dialog:pick-directory` 返回用户选中的路径 → 界面调
`workdir.bind {grant: true}` → `ai.bind_mode_workdir(grant=True)` →
`ModeRegistry.bind_workdir(grant=True)`。任何一环不传 grant 都退回到拒绝语义。

**验证**：绑定后 `PathPolicy.is_writable` 对该目录内文件返回 True，对目录外
路径仍返回 False（授权只放开选中的那棵树，不是全盘放行）。

### 9.3 工作区 / 权限控件移到输入框旁

对齐昔涟 `ChatComposer` 的 footer 布局。新增 `ComposerToolbar`，渲染在输入框
**正上方**一行：

- **工作目录**：显示缩略路径（`…/` + 末两段），点击弹目录对话框换目录，
  旁边 `×` 解绑；
- **权限**：下拉三选（只读 / 工作区可写 / 完全访问），每项带一句后果说明；
  选中即写 `modes.<mode>.sandbox` 并刷新工具上下文。

右侧 `ModeWorkspace` 相应收窄为**只读执行现场**（当前目录、沙箱、本轮工具
调用），不再承担配置职责——避免两处入口各改一半、状态不一致。

chat 模式不显示工作目录按钮（`WorkdirPolicy.bindable=False`）。

**失败反馈**：绑定失败（例如后端拒绝）走全局 `Toast`，不再静默——用户点了
按钮必须有回应。


## 10. 第四轮反馈（气泡对齐与换行）

### 10.1 最大化后左右不对齐

**根因**：消息容器有 `mx-auto max-w-3xl`（768px）。窗口最大化后内容区变宽，
容器仍居中且被限制在 768px，于是两侧各留一大片空白，气泡看起来「缩在中间」。

**修复**：去掉 `max-w-3xl` 与 `mx-auto`，容器改为 `w-full`。可读性不再靠压缩
整个内容区，而是由**每条气泡自身的宽度上限**保证：

| 元素 | 宽度约束 |
| :--- | :--- |
| 用户气泡 | `max-w-[min(78%,640px)]`，整行 `justify-end` 贴右 |
| AI 气泡 | `max-w-[min(78%,680px)]`，左对齐贴左 |
| 工具条 | `w-fit max-w-full`（内容多宽就多宽，超长才截断） |
| 表情包 | `w-fit max-w-[200px]` |

### 10.2 短消息被拆成两行

**根因**：AI 侧的列容器是 `flex min-w-0 flex-col`——`min-w-0` 让它可以收缩到
0，而 `flex-col` 的宽度是 **fit-content**。于是 `max-w-[70%]` 里的 70%
参照的是一个「由内容决定、且允许缩到 0」的宽度，形成循环依赖，最终塌缩成
很窄的一条：短消息被迫折行，工具条被压成「rea…」「get_curr…」。

**修复**：给该列加 `flex-1`，让它先撑满剩余宽度、百分比才有确定的参照：

```
flex min-w-0 flex-1 flex-col      ← 先撑满（flex-1），再允许收缩（min-w-0）
```

这是 flex 布局里 `min-w-0` + 百分比子元素的经典陷阱：**只加 min-w-0 而不给
flex-1，父级就是 fit-content，任何百分比子元素都会塌缩**。

### 10.3 验证

在 1920×1000（模拟最大化）与 1080×720（带右侧面板的实际尺寸）两种窗口下
测量：

- 用户气泡右边缘 = 0（贴右），AI 气泡左对齐；
- 短消息 `"好"` 宽度 46px、单行；
- 工具条宽度恢复到 146 / 134 / 209 / 197px（不再被压成省略号）。


## 11. 退出后 sidecar 残留（孤儿进程）

### 11.1 症状

关闭 Electron 后，任务管理器里仍有 `python.exe` / `castorice-sidecar.exe`
常驻。用户表述为「关了程序 python 后台不能自己结束」。

### 11.2 根因（两层）

**① Windows 没有「父死子亡」机制。**
POSIX 下父进程退出后子进程会被 reparent 到 init 并收到 SIGHUP（取决于
配置）；Windows **完全没有这回事**——子进程会一直活着直到自己退出或被杀。
原来只有 `app.on("before-quit")` 里调 `sidecar.stop()`，这条路径在
「任务管理器强杀 / Electron 崩溃 / 关机」时根本不执行。

**② 只杀外层进程，内层 Python 变孤儿。**
PyInstaller `--onedir` 产出的 `castorice-sidecar.exe` 是**引导器**，真正
干活的 Python 解释器是它的子进程。原来的 `proc.kill()` 只杀引导器，
内层 python 继续跑——这解释了为什么有时「exe 没了但 python 还在」。

### 11.3 修复：三层兜底

**第一层 —— sidecar 自杀（覆盖父进程被强杀）**

`spawn-bridge.py` 新增 `_install_parent_watchdog()`，在**任何重活之前**
安装（chromadb 加载要几十秒，这期间父进程被杀的窗口必须覆盖）：

| 机制 | 触发条件 | 说明 |
| :--- | :--- | :--- |
| stdin EOF | 父进程退出 → 管道关闭 | 立即退出，最可靠 |
| PPID 轮询 | 每秒检查一次 | 兜住 stdin 被继承/未关闭的情况 |

父进程判断用 `OpenProcess` + `GetExitCodeProcess`（`PROCESS_QUERY_LIMITED_INFORMATION`），
**不用 `os.kill(ppid, 0)`**——Windows 上该调用语义不可靠，会误判。

判断不了时（异常 / 权限不足）**一律按存活处理**：宁可多活一秒，不能误杀
自己把用户正在用的对话打断。

**第二层 —— Electron 主动收（覆盖正常退出）**

- `spawn` 的 `stdio[0]` 从 `"ignore"` 改成 `"pipe"`：这是第一层的前提，
  `ignore` 的话管道不存在，sidecar 永远读不到 EOF；
- 通过 `CASTORICE_PARENT_PID` 把 `process.pid` 传给 sidecar；
- `stop()` 改为三段：**关 stdin** → SIGTERM → 3s 后 `killTree`；
- `killTree` 在 Windows 用 `taskkill /T /F` **连带子进程**，解决根因②；
- 退出钩子从 `before-quit` 扩展到 `will-quit` + `process.on("exit")`
  （`stop()` 幂等，重复调用无副作用）。

**第三层 —— 双保险的意义**

第一层管「壳被杀」，第二层管「壳正常退」。任一层失效另一层仍能收尾；
两层都不依赖对方，避免单点。

### 11.4 验证（真实进程，三场景）

| 场景 | 期望 | 结果 |
| :--- | :--- | :--- |
| A. 父进程正常退出 | stdin EOF → 子进程退出 | PASS |
| B. 父进程被强杀（不关 stdin） | PPID 轮询 → 子进程退出 | PASS |
| C. 父进程存活 | **子进程不得被误杀** | PASS |

场景 C 是防止「修好一个 bug 引入另一个」——看门狗误判会把用户正在进行的
对话杀掉。验证时还发现探针自身的一个假阳性（C 误用 normal 模式导致父进程
提前退出），修正后通过。

### 11.5 已知限制

`process.on("exit")` 阶段只能发信号，不能等待；若 `taskkill` 本身没能
及时执行，仍有极小窗口残留。真正的兜底是第一层的 PPID 轮询（1s 内生效），
它不依赖壳层任何代码执行。


## 12. 程序与数据分离（A）+ 增量补丁（B）

### 12.1 问题

更新方式是「整个程序文件夹替换」，而 v1.4 及以前所有可写数据都在
`resources\backend\` 里：

| 数据 | v1.4 位置 |
| :--- | :--- |
| API Key / 模型配置 | `config.yaml` |
| 用户档案 | `profile.json` |
| 外观与风格 | `ui_settings.json` |
| 记忆向量库 | `database\` |
| 对话历史 | `sessions\` |

**覆盖更新 = 配置全丢。** 这不是打包技巧能绕过的，必须先把数据和程序分开。

### 12.2 A 部分：数据外置

新增 `castorice/paths.py`，成为所有可写路径的**单一解析点**：

```
解析顺序：CASTORICE_DATA_ROOT（壳层传入 %APPDATA%）> 程序目录（dev 兼容）
```

| 类别 | 位置 | 理由 |
| :--- | :--- | :--- |
| config / profile / ui_settings / database / sessions / channels | **数据目录** | 用户可写，更新时不能动 |
| prompts / Image / 代码 | **程序目录** | 随版本发布的只读素材 |

Electron 侧 `app.getPath("userData")` 即 `%APPDATA%\CastoriceAgent`，
经 `CASTORICE_DATA_ROOT` 传给 sidecar。dev 与 prod 用同一位置，避免
「开发时数据在仓库、发布后在 APPDATA」的割裂。

**首次启动播种**：数据目录缺 `config.yaml` 时从程序目录的模板复制一份。
只播种**不存在**的文件——已存在的绝不覆盖，否则「升级后配置重置」会
以另一种方式回来。

**旧数据迁移**（`castorice/migration.py`）：老用户升级时，
`config.yaml` / `profile.json` / `ui_settings.json` / `database` /
`sessions` / `channels` 从程序目录复制到数据目录，**仅当目标不存在**。
顺序是先播种后迁移：新用户靠播种拿模板，老用户靠迁移保住配置，两者不冲突。

**顺带修掉的泄露风险**：v1.4 的 `extraResources` 会把开发机上的
`profile.json` / `ui_settings.json` / `database` / `sessions` 一起打进
发布包（本仓库的开发目录里确实存在这些文件）。打包脚本现在显式删除它们，
并在发布目录放一份《数据位置说明.txt》。

### 12.3 B 部分：增量补丁

`scripts\打包补丁.ps1`，按 git diff 收集变更文件并映射到发布目录路径：

| 仓库路径 | 发布包路径 |
| :--- | :--- |
| `castorice/**` | `resources\backend\castorice\**` |
| `ai.py` / `main.py` | `resources\backend\` |
| `prompts/**` | `resources\backend\prompts\**` |
| `Image/**` | `Image\**` |
| `webapp/**` | `resources\app.asar`（整体替换） |
| `scripts/ docs/ tests/` | 不收 |
| `config.yaml` | 不收（程序目录那份是模板） |

**sidecar 按需重发**：PyInstaller 产物几百 MB，只有后端运行逻辑
（`harness` / `bridge` / `ai.py` / `spawn-bridge.py` 等）改动时才打包
`backend-runtime`；纯前端改动可以只发 asar（几 MB）。

用法：

```powershell
pwsh -File scripts\打包补丁.ps1                      # 上一个 tag -> HEAD
pwsh -File scripts\打包补丁.ps1 -From v1.5 -To HEAD
pwsh -File scripts\打包补丁.ps1 -DryRun               # 只看收哪些文件
```

**打包格式**：用 `System.IO.Compression.ZipFile` 生成**真正的 zip**
（v1.4 的「.zip 实际是 tar」问题不再出现）。包内附《补丁说明.txt》，
写清安装步骤、数据安全说明与回滚方式。

### 12.4 验证

| 项 | 结果 |
| :--- | :--- |
| 新用户从模板播种 | PASS |
| 老用户数据迁移（6 类） | PASS |
| 二次迁移不覆盖用户新改的内容 | PASS |
| 代码根 == 数据根时不迁移（dev） | PASS |
| 细粒度 ENV（`CASTORICE_PROFILE_PATH`）仍优先 | PASS |
| 模拟「覆盖更新后」真实 `AI` 仍读到用户 Key | PASS |
| 补丁路径映射（v1.4→HEAD 实测） | 源码进包、文档/测试排除、config 排除 |
| 全量测试 | 788 passed / 1 skipped |


## 13. v1.5.1 热修复：添加提供商无效

### 13.1 症状

在「模型 API」页点「添加」→ 选厂商 → 填 Key → 确定，**界面提示添加失败，
但用户此前没做任何操作**；或看起来成功但列表里没有新条目。

### 13.2 根因

`config.set` 底层是 `update_yaml_scalars`，它只能**定位已存在的键**并
替换其标量值。对一个全新的提供商：

1. `providers.<name>` 这个父块根本不存在 → `_locate_yaml_key` 返回 None；
2. 代码走到「追加到父块末尾」分支，找 `parent = ("providers",)`；
3. 找到 providers 段了，于是把字段插进去……

但**三个字段的插入互相看不到**：同一批 updates 里先插
`providers.mybrand.api_key` 时，父块 `providers.mybrand` 还不存在，
直接 `continue` 跳过。结果三个字段全部静默丢弃，函数返回成功，
接口返回 `{"ok": true}`。

**最恶劣的点是「静默」**：既不报错也没生效，用户和界面都无从判断。

### 13.3 修复

**① 新增 `provider.add` RPC（整块写入）**

`ConfigManager.set_provider_values` 先调用 `ensure_provider_block`
补一个空的父块行（`  mybrand:`），再逐字段 set——复用既有的
「保留注释与排版」逻辑，不必重写整份 YAML。

**② `config.set` 对未命中路径显式报错**

新增 `update_yaml_scalars_ex`（返回未命中的路径列表），
`ConfigManager.set_values` 发现有未命中就抛 `KeyError`：

```
以下配置路径在 config.yaml 中不存在，未能写入: providers.nosuch.api_key
```

保留旧的 `update_yaml_scalars` 签名（委托给 ex 版本取第一个返回值），
既有调用方零改动。

**③ 前端显示真实原因**

`settings-api.addProvider` 返回 `{ok, error}` 而不是布尔值，
失败时把后端原文显示出来，不再用「添加失败（同名提供商可能已存在）」
这种**猜出来的文案**——它在这次事故里完全误导了排查方向。

### 13.4 验证

| 项 | 结果 |
| :--- | :--- |
| `provider.add` 新增后真的出现在配置里 | PASS |
| 字段值正确 | PASS |
| `config.set` 对不存在路径抛异常（不再静默） | PASS |
| 修改已存在的键仍正常 | PASS |
| 新增后逐字段保存（保存按钮路径）可用 | PASS |
| 原有注释与排版未被破坏 | PASS |
| 全量测试 | 788 passed / 1 skipped |

### 13.5 教训

**「操作成功但没效果」比「直接报错」危害大得多。** 静默失败让排查成本
从「看一眼错误信息」变成「逐层加日志复现」，而且会误导用户以为是自己的
操作问题。本项目此后遵循：写操作要么生效，要么抛错，不允许第三条路。


## 14. v1.5.2 热修复：打包版启动卡死

### 14.1 症状

用户报告「启动后端花了 1 分钟还没有好」。实测打包版 **240 秒仍未 ready，
进程 0% CPU、46MB 内存、不退出**。

### 14.2 定位过程（不是猜的）

| 步骤 | 结论 |
| :--- | :--- |
| 源码跑同一入口 | 21 秒正常 ready |
| 打包版加 faulthandler 看门狗 | 卡在 `numpy\_core\multiarray.py` 的 import |
| 最小包（只 import numpy） | 5.2 秒正常 |
| mini2（完整 collect-all + import ai） | 4.5 秒正常 |
| repro3（复刻全部导入顺序 + BridgeServer） | 18.7 秒正常 |
| repro4（**加回看门狗线程**） | **卡死，复现** |

**根因：`sys.stdin.readline()` 在 PyInstaller 冻结环境里会死锁。**

一个长期阻塞在 `readline()` 的守护线程，会在后续 import 阶段与
PyInstaller 的 importer 互锁：importer 等 IO 锁，readline 线程持有它不
释放。表现就是**进程还活着、CPU 为 0、永远不 ready**。

这解释了很多反直觉现象：单独 import numpy 正常、单独 import ai 正常、
两个都有但没有 stdin 线程也正常——只有「stdin 读线程 + 打包版 import」
同时存在才触发。

### 14.3 修复

**stdin 监控改用 `PeekNamedPipe` 轮询**（不阻塞、不持有 IO 锁）：

```python
avail = wintypes.DWORD(0)
ok = kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(avail), None)
if not ok:          # 管道断开 = 父进程走了
    os._exit(0)
```

同时**彻底不碰 `sys.stdin` 对象**：打包环境里访问它会初始化 Python 的
IO 层，是死锁的另一半诱因。句柄类型判断也改成纯 ctypes
（`GetStdHandle` + `GetFileType`）。

### 14.4 验证

| 项 | 结果 |
| :--- | :--- |
| 打包版 ready 耗时 | **21.5 秒**（原：240 秒卡死） |
| 父进程退出后自动结束 | 1.0 秒内（孤儿防护仍有效） |
| 各阶段耗时 | import ai 19.1s、AI() 3.08s |

### 14.5 顺带加的启动诊断

`spawn-bridge.py` 现在会记录：
- `import ai 完成，耗时 X.XXs`
- `AI() 构造完成，耗时 X.XXs`
- 启动超 90 秒自动 dump 全部线程栈（`CASTORICE_STALL_DUMP` 可调阈值）

下次再出现「启动慢」，日志可直接定位到具体阶段，不必再从零复现。

### 14.6 教训

**「阻塞式 IO + 打包环境」是高风险组合。** 在源码里完全正常的
`sys.stdin.readline()`，到了 PyInstaller 冻结环境里会与 importer 互锁。
后续凡是引入长期阻塞线程，都必须验证打包版行为——源码通过不等于发布版通过。

