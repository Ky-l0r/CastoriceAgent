<div align="center">

# CastoriceAgent · 遐蝶

**一个跑在 Windows 桌面上的 AI 陪伴助手**

有性格、有情绪、记得住事 —— 不是网页聊天框的套壳。

![平台](https://img.shields.io/badge/平台-Windows_10%2F11-0078D6?logo=windows&logoColor=white)
[![下载](https://img.shields.io/badge/下载-Releases-2ea44f?logo=github&logoColor=white)](https://github.com/Ky-l0r/CastoriceAgent/releases)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

---

## 这是什么

CastoriceAgent（中文名「遐蝶」）是一个装在 Windows 电脑上的桌面 AI 助手。

它和网页版 AI 聊天有几个不一样的地方：

- **有固定的性格** —— 说话方式、称呼、语气都经过设定，不会聊两句就变回客服腔；
- **会表达情绪** —— 回复之后可能附带一张表情包，而且挑的是和当时情绪对得上的那张；
- **记得住事** —— 你们的对话会存在本机，下次聊天时它能把相关的事想起来，还知道那是**什么时候**说的；
- **能查能搜** —— 需要时会自己联网查资料，拿到内容再回答。

程序是**绿色便携**的：下载一个文件夹，双击里面的 exe 就能用。
不需要安装 Python，不需要懂编程，也不会往系统里写注册表 —— 不想要了，整个文件夹删掉就干净了。

---

## 界面预览

| 深色（黑色偏紫） | 浅色（白色偏紫） |
| :---: | :---: |
| ![深色主题](docs/screenshot-dark.png) | ![浅色主题](docs/screenshot-light.png) |

界面风格可以随时切换，默认跟着 Windows 的深色 / 浅色设置走。

---

## 快速开始

### 系统要求

| 项目 | 要求 |
| :--- | :--- |
| 系统 | Windows 10 / 11（64 位） |
| 磁盘 | 约 400 MB |
| 网络 | 需要联网（AI 模型在云端运行） |
| 其它 | 无需安装 Python，无需管理员权限 |

### 第一步：下载并解压

到 [Releases 页面](https://github.com/Ky-l0r/CastoriceAgent/releases) 下载最新版本的压缩包，
解压到**任意有写入权限的位置**（比如桌面、D 盘下的一个文件夹）。

> ⚠️ 两个注意点：
> 1. **要解压整个文件夹**。exe 依赖它旁边的 `_internal` 文件夹，单独把 exe 拖出来是不能运行的。
> 2. **不要放在 `C:\Program Files` 这类需要管理员权限的目录**，否则程序无法创建记忆库和日志文件。

### 第二步：启动

双击 `CastoriceAgent.exe`。

- 首次启动会慢几秒（要加载本地检索库），属正常现象。
- 如果 Windows 弹出蓝色的「Windows 已保护你的电脑」提示，点 **更多信息 → 仍要运行** 即可。
  这是因为程序没有购买数字签名证书，并非病毒。

### 第三步：填一次 API Key

遐蝶本身是个「壳」，真正负责思考的 AI 模型需要你提供一个 API Key。**这一步只需做一次。**

首次启动时，程序发现你还没填 Key，会**自动弹出「设置」窗口**，按下表填完点保存就行：

| 字段 | 怎么填 |
| :--- | :--- |
| **提供商** | 下拉选一个，如 `deepseek` |
| **API Key** | 填你自己的密钥（形如 `sk-xxxxxxxx`） |
| **Base URL** | 一般不用改，换服务商时才需要 |
| **模型** | 一般不用改 |
| **Temperature** | 可留空，默认即可 |

Key 要去 AI 服务商的官网注册后自行创建，例如：

- **DeepSeek** —— <https://platform.deepseek.com>（默认提供商，价格便宜，适合新手）
- **阿里云百炼（通义千问）** —— <https://bailian.console.aliyun.com>
- **[OI]** —— <https://platform.openai.com>
- 任何**兼容 [OI] 接口**的服务都可以用（月之暗面、本地 Ollama……）

> 💰 本软件免费，但调用 AI 模型是**按量计费**的，需要你先在服务商那边充值。
> 日常聊天花不了多少钱，具体价格见服务商官网。

填好保存、关掉设置窗口，就可以开始聊天了。

---

## 它能做什么

**🎭 完整的角色扮演系统**
角色设定拆成 11 个模块（人格核心、语气规则、世界观、人物关系、原作台词……），按触发词动态注入。
想换成别的角色？替换 `prompts\` 里的文件即可，不用改代码。

**💭 会看情绪发表情**
回复写完后，程序按情绪关键词从表情库里挑一张贴纸发出来 —— 开心时不会发哭泣的图。
带概率与冷却控制，不会每条都刷表情。情绪匹配不上时也会从「温和表情」里随机兜底。

**🧠 记得住事，还知道是什么时候说的**
对话写进本机的记忆库，下次聊天自动检索相关记忆。
每条记忆都记着发生时间，所以它能说出「你上周提过……」而不只是「你提过……」。
每轮对话还会注入当前真实时间，问「现在几点」不用查也能答对。

**🔧 能动手，不只动嘴**
需要时会自己联网搜索（支持 Bing / DuckDuckGo / Tavily / Serper / Bing API / 博查 六种后端，可自动降级），
拿到资料再回答。

**🖼️ 能看懂图片**
换到视觉模型后，输入区会出现「图片」按钮。选好的图先挂在输入框上方，可以配文字一起发。
图片会自动压缩到合适尺寸再上传，省流量也省费用。

**🎨 认真做的界面**
无边框圆角窗口、自绘按钮图标、流式打字机输出、消息淡入展开动画、平滑滚动。
深浅两套配色（都偏紫），默认跟随系统，右上角齿轮里随时切换。

**⚙️ 不用手改配置文件**
换提供商、填 API Key、改模型、选主题，都在设置窗口里点。保存时会写回配置文件，**原有注释和排版一字不动**。

---

## 日常操作

| 操作 | 怎么做 |
| :--- | :--- |
| **发送消息** | `Enter` 发送，`Shift + Enter` 换行 |
| **发表情包** | 点输入框旁的笑脸按钮，选中后插入输入框，可继续补文字 |
| **发图片** | 点图片按钮选图，图片会挂在输入框上方（点 × 移除），可配文字一起发 |
| **切换主题** | 右上角齿轮 → 外观 → 跟随系统 / 浅色 / 深色 |
| **复制消息** | 鼠标直接拖选文字即可 |
| **最小化 / 关闭** | 右上角标题栏按钮；窗口可按住标题栏拖动 |

> 💡 图片功能需要**视觉模型**。程序会按模型名自动判断（`*-vl`、`gpt-4o`、`claude-3` 等视为视觉模型），
> 也可以在「设置」里手动勾选「该模型支持图片输入」。

---

## 常见问题

<details>
<summary><b>双击没反应 / 提示缺文件</b></summary>

多半是没解压完整。请确认 `CastoriceAgent.exe` 旁边还有一个 `_internal` 文件夹，
并且是从压缩包里**整个文件夹**解压出来的，而不是只把 exe 拖出来。
</details>

<details>
<summary><b>杀毒软件报毒 / Windows 提示「已保护你的电脑」</b></summary>

这是没有数字签名的 exe 常见的误报，PyInstaller 打包的程序尤其容易遇到。

- 蓝色提示「Windows 已保护你的电脑」：点 **更多信息 → 仍要运行**。
- 被安全软件拦截：把它加入信任 / 白名单。
- 仍不放心的话，可以自己从源码打包一份（见文末「开发者」章节）。
</details>

<details>
<summary><b>发消息报 401 / 鉴权失败</b></summary>

API Key 没填或填错了。打开右上角设置重新填写。
也检查一下服务商账户里是否还有余额。
</details>

<details>
<summary><b>提示模型不存在</b></summary>

模型名写错了。到服务商官网查准确的模型名（如 DeepSeek 的 `deepseek-chat`），在设置里改「模型」一栏。
</details>

<details>
<summary><b>回复很慢或没反应</b></summary>

先看程序目录下的 `logs\app.log`。如果是网络问题，检查 `Base URL` 是否能正常访问 ——
部分服务商在境内需要特殊网络环境才能连通。
</details>

<details>
<summary><b>看不到「图片」按钮</b></summary>

说明当前模型不被识别为视觉模型。换一个支持图片的模型（如 `gpt-4o`、`*-vl` 系列），
或者在「设置」里手动勾选「该模型支持图片输入」。
</details>

<details>
<summary><b>AI 为什么有时候不发表情包</b></summary>

这是故意的。默认约三成概率附带表情，避免每条都刷图。
想调高就把 `config.yaml` 里 `emoji.probability` 改成 `0.8`，或设成 `1.0` 让它每条都发。
</details>

<details>
<summary><b>界面太暗 / 太亮</b></summary>

点右上角齿轮 → 外观，可选「跟随系统 / 浅色 / 深色」。选择会记住，下次启动自动恢复。
</details>

<details>
<summary><b>怎么清空聊天记忆</b></summary>

删掉程序目录下的 `database\` 文件夹，下次启动会自动重建一个空库。
</details>

<details>
<summary><b>启动有点慢</b></summary>

文件夹版首次启动约 2~5 秒，要加载本地检索库（onnxruntime 等），属正常现象。
启动过一次后会快一些。
</details>

---

## 进阶：换成你自己的角色

不用改任何代码，改 `prompts\` 里的文本文件就行。这些 `.md` 会按文件名顺序拼接成 AI 的「人设」：

| 文件 | 作用 |
| :--- | :--- |
| `identity.md` | 角色定位与工作边界 |
| `soul.md` | 人格核心：她是谁、怎么说话、如何表达情感 |
| `tone-rules.md` | 语气规则 |
| `system.md` / `talk_system.md` | 系统规则 |
| `world.md` / `story.md` / `characters.md` | 世界观、经历、相关人物（按触发词注入） |
| `Castorice.md` | 她自身的身世与本体条目（按触发词注入） |
| `canon_quotes.md` | 原作台词，用来校准说话风格 |
| `appellation.md` | 称谓统一映射 |

**最省事的做法**：只改 `identity.md` 和 `soul.md`，其余留空或删掉。

想换掉头像，把 `Image\CastoriceAvatar.jpeg` 替换成你自己的同名图片即可。

### 加自己的表情包

1. 把图片（`png` / `jpg` / `jpeg` / `webp` / `gif` / `bmp`）丢进 `Image\表情包\`，
   **文件名就是表情名**，例如 `开心.png`。
2. 想让它在合适情绪下被选中，在 `config.yaml` 里补上关键词：

```yaml
emoji:
  keywords:
    开心: ["开心", "高兴", "太好了", "哈哈"]
```

不配也能用，只是会走随机兜底。

---

## 高级设置（编辑 config.yaml）

设置窗口能改的都在界面上，剩下这些需要直接编辑程序目录下的 `config.yaml`。
**改完保存，重启程序生效。**

### 表情包频率

```yaml
emoji:
  enabled: true          # 总开关
  probability: 0.35      # 发出概率，0.35 ≈ 三成回复带表情
  cooldown_turns: 0      # 发过一次后至少隔几轮
  max_per_session: 0     # 每次启动最多发几张，0 为不限
```

### 联网搜索

```yaml
search:
  provider: "bing_html"  # bing_html / ddg_html / tavily / serper / bing_api / bocha / auto
  api_key: ""            # 用付费后端时填（也可用环境变量 SEARCH_API_KEY）
  max_results: 5
  fetch_content: true    # 是否抓取网页正文（让模型拿到摘要而非只有链接）
  fetch_top_n: 2
```

前两种（`bing_html` / `ddg_html`）是免费免密钥的网页搜索，`auto` 会依次尝试并在失败时自动降级。

---

## 数据、备份与卸载

程序是绿色便携的，**所有数据都在它自己的文件夹里**，不往别处写：

| 文件 / 文件夹 | 内容 |
| :--- | :--- |
| `config.yaml` | 你的设置，**含 API Key** |
| `database\` | 对话记忆库（自动生成） |
| `ui_settings.json` | 外观偏好（自动生成） |
| `logs\app.log` | 运行日志，出问题时先看这里 |

- **备份**：把整个文件夹复制走就行。
- **清空记忆**：删掉 `database\`。
- **卸载**：把整个文件夹删掉，不留任何残留。

> ⚠️ **分享给别人之前**，记得先把 `config.yaml` 里的 API Key 删掉或清空 ——
> 里面存的是你的密钥，别人拿到可以直接用你的额度。

---

## 致谢与版权

- 界面基于 [PySide6](https://doc.qt.io/qtforpython/)（Qt for Python）
- 记忆检索使用 [ChromaDB](https://www.trychroma.com/)
- 模型调用通过 [[OI] Python SDK](https://github.com/openai/openai-python) 的兼容接口

角色「遐蝶 / Castorice」出自游戏《崩坏：星穹铁道》，**版权归米哈游所有**。

---

## 许可

本项目**源代码与文档**采用 [MIT License](LICENSE) 开源，你可以自由使用、修改、分发，只需保留版权声明。

不过有一点需要留意：**仓库里的角色素材不在 MIT 授权范围内** ——

| 内容 | 授权情况 |
| :--- | :--- |
| `ai.py`、`ui.py`、`main.py` 等源码，`README.md`、`打包.ps1` 等文档脚本 | MIT，自由使用 |
| `prompts/` 中转述游戏角色设定与原作台词的文本 | 出自《崩坏：星穹铁道》，版权归米哈游 / HoYoverse |
| `Image/` 下的头像与表情包图片 | 同上 |

这些素材仅用于**个人非商业的学习与同人用途**。如果你想二次分发，或者要把它用于商业场景，
请自行替换成自己的角色设定与图片素材 —— 程序结构上是完全支持的（见上方「换成你自己的角色」）。

> `LICENSE` 文件为纯 MIT 标准文本，以便 GitHub 正确识别许可证类型；
> 关于第三方素材的边界说明以本节为准。

---

<details>
<summary><b>🛠️ 开发者：从源码运行 / 自行打包</b></summary>

<br>

如果你要改代码，或者想自己打一份 exe，看这里。**只是使用的话，上面就够了。**

### 环境要求

- **Python 3.9+**（开发环境为 3.13）
- **Windows**（界面基于 PySide6，理论上跨平台，但未在 Linux / macOS 上测试）

### 从源码运行

```bash
git clone https://github.com/Ky-l0r/CastoriceAgent.git
cd CastoriceAgent
pip install -r requirements.txt
```

或者直接运行附带脚本：

```bash
python scripts/requirement_install.py
```

然后：

```bash
python main.py
```

想先看界面不接 API？直接运行 `python ui.py`，会用内置的模拟 AI 演示界面效果。

### 打包为 exe

```powershell
pwsh -File scripts\打包.ps1
```

产物在 `发布包\CastoriceAgent\`，整个文件夹拷给别人就能用。
脚本会自动生成并嵌入 exe 图标（取自 `Image\CastoriceAvatar.jpeg`），
发布包里的 `config.yaml` 用示例配置占位，不含任何密钥。

脚本已经排除了 `torch`、`scipy`、`pandas`、`transformers` 这几个 **chromadb 声明里没有、运行时也不导入**的依赖 ——
它们是被 `--collect-all` 连带收进来的，其中 `torch_cpu.dll` 单个文件就有 291MB。排除后发布包从 871MB 降到 **336MB**。

### 项目结构

```
CastoriceAgent/
├── main.py                     程序入口：环境准备、日志、首次使用引导
├── ui.py                       界面：主窗口、设置窗口、气泡、表情面板、动画
├── ai.py                       AI 核心：配置、记忆库、提供商适配、工具、表情包
├── requirements.txt            依赖清单
├── example_config.yaml         配置模板（发布时用它占位）
├── config.yaml                 你的实际配置（已被 .gitignore 忽略）
├── scripts/
│   ├── 打包.ps1                一键打包脚本
│   └── requirement_install.py  依赖安装脚本
├── docs/
│   ├── 使用说明.txt             给最终用户的说明书（随发布包分发）
│   ├── screenshot-dark.png     界面截图（深色）
│   └── screenshot-light.png    界面截图（浅色）
├── prompts/                    角色设定（11 个模块）
├── Image/
│   ├── CastoriceAvatar.jpeg    聊天头像
│   ├── CastoriceAgent.ico      exe 图标（打包脚本自动生成 / 使用）
│   └── 表情包/                 表情贴纸，文件名即表情名
├── database/                   对话记忆库（自动生成）
└── logs/app.log                运行日志
```

</details>
