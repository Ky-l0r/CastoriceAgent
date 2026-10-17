# 测试套件

CastoriceAgent 的回归测试基线。改造 `ai.py` / 桥接层之前先跑一遍，
改造之后再跑一遍，用于确认对外接口与既有语义没有被破坏。

> **v1.4 基线调整（Qt 退役）**：界面从 PySide6 窗体迁移到 Electron 壳 +
> Web UI（webapp/），`ui.py` 删除，Qt 专属用例同步退役并从基线扣除：
>
> | 退役文件 | 用例数 | 说明 |
> | --- | --- | --- |
> | `tests/test_ui_panels.py` | 48 | ChatWindow / 面板 / 打字机 / UiSettings 落盘（Qt 窗体专属） |
> | `tests/test_approval_wiring.py` | 31 | 审批接线（ChatWindow 应答者注册 / 线程纪律 / 颜色）；其中纯逻辑审批语义由 `test_permissions.py`（64 用例）与 `test_bridge.py`（Web 审批 RPC 23 用例）承接 |
>
> 818 - 79 = **739** 为新基线（此后新增 `test_bridge.py` 23、`test_profile.py`
> 27 等按叠加计数）。Web 侧 UI 行为由 `webapp/smoke-ws.py` 端到端冒烟覆盖。

## 运行方式

在**仓库根目录**执行（不要在 `tests/` 里面执行）：

```bash
# 运行全部测试
python -m pytest

# 或者（等价，pytest 会通过根目录 conftest.py 找到仓库根）
pytest
```

常用变体：

```bash
# 只跑某个文件
python -m pytest tests/test_yaml_update.py

# 只跑名字里含 emoji 的用例，并显示每个用例名
python -m pytest -k emoji -v

# 停下来看第一个失败的细节
python -m pytest -x

# 显示最慢的 10 个用例
python -m pytest --durations=10
```

### 依赖

只需要 `requirements.txt` 里的依赖，再加 `pytest`：

```bash
pip install -r requirements.txt
pip install pytest
```

## 硬性约定

1. **不需要 API Key、不需要联网**：全部用例都能离线跑通。
   `tests/conftest.py` 里的 `block_network` 夹具默认把 `socket.connect`
   拦掉，任何真实连接都会直接失败。确实需要联网的用例必须显式标注
   `@pytest.mark.allow_network`。
2. **不写仓库文件**：测试需要配置文件时用 `write_config` 夹具写到
   pytest 的临时目录，绝不动仓库里的 `config.yaml` 和 `database/`。
3. **不替换业务实现**：只允许 patch 随机数（`ai.random`）、网络层
   （`socket.socket`）和测试内自造对象；不允许给 `ai.py` / `ui.py`
   打桩来「制造」通过。`test_offline_guarantees.py` 会自动扫描并拦截越界用法。
4. **改接口必须同步改测试**：`test_backward_compat.py` 是给后续改造者的护栏，
   若确实要调整对外契约，请在同一个改动里更新这些断言并在提交里说明原因。

## 目录结构

```
conftest.py                          # 把仓库根加入 sys.path（脚本式布局必需）
pytest.ini                           # pytest 配置：testpaths / markers
tests/
  conftest.py                        # 公共夹具：禁网、临时配置
  README.md                          # 本文件
  test_yaml_update.py                # update_yaml_scalars：写回时保留注释与排版
  test_config_manager.py             # ConfigManager.get_provider_config / set_active_provider
  test_emoji.py                      # EmojiCatalog.match_by_mood、EmojiSession.should_append
  test_html_to_text.py               # _html_to_text 的标签/实体/空白/截断行为
  test_tools.py                      # ToolRegistry、内置工具、搜索结果格式化
  test_backward_compat.py            # AI / AIFactory / get_ai / get_response_stream 契约
  test_offline_guarantees.py         # 离线与隔离性自检
```

## 覆盖范围

| 模块 | 覆盖点 |
| --- | --- |
| `update_yaml_scalars` | 只改目标行、保留行尾注释与缩进、缺失键插入父块末尾、缺失父块时跳过、标量转义、换行符保持、结果可被 YAML 解析回读 |
| `ConfigManager` | `get_provider_config`（含未知提供商报错、空 api_key 仍算已配置）、`set_active_provider`（内存 + 落盘 + 注释保留 + 幂等）、`set_provider_values`、`reload`、损坏 YAML 报错 |
| `EmojiCatalog.match_by_mood` | 空输入返回 None、关键词打分取最高、同分随机且不越界、单字关键词忽略、无贴纸时不参与、兜底避开强烈情绪、目录扫描规则（表情包子目录 / `_` 前缀 / 头像 / 同名多图） |
| `EmojiSession.should_append` | 概率上下界与钳制、第一轮必发、冷却窗口内不发、冷却过期后恢复、`cooldown_turns=0`、`max_per_session` 上限、判定无副作用、`reset` 语义 |
| `_html_to_text` | 去标签、去 script/style/注释、实体反转义、空白折叠、按 `max_len` 截断、空输入 |
| 工具生态 | `ToolRegistry` 注册/覆盖/调用、未知工具与异常的错误文本、OpenAI function spec 形状、内置工具清单、`format_search_results` 截断 |
| 对外契约 | `AI` 的构造签名与公开方法、`get_response_stream` 是生成器且事件类型固定为 text / tool / emoji / done、`get_ai` / `AIFactory` 签名 |

## 后续改造怎么用

- 新增四模式内核时，把 Chat / Work / Code / Learn 的纯逻辑放进独立模块，
  并在 `tests/` 下补对应的 `test_*.py`，保持「不联网即可跑」的约束。
- 权限审批网关建议以「策略对象 + 纯函数判定」的形式实现，便于直接单测
  allow / deny / ask 三种结果，而不必真的弹窗。
- 记忆引擎若引入 ChromaDB，请用 `tmp_path` 建临时库，避免污染 `database/`。
- 每次提交前跑 `python -m pytest -q`，确保基线始终是绿的。
