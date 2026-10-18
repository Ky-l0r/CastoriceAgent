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

