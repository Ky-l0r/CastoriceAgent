"""组合根（t20 收口）：运行期目录约定 + 部件装配 + 配置节补齐。



02-architecture.md §2.7 原本设想把 ai.AI 的全部装配抽到这里。第一期定稿

时装配逻辑由 t3/t4/t11/t13/t14/t15/t17 各轮修复固化在 ai.AI.__init__ 内

（模式注册表 / 权限网关 / 会话 / harness 的组装顺序经过多轮验证），本任务

是「核对 + 文档 + 集成」，不重复实现——因此这里提供三件收口设施，而不是

重写装配：



1. AppPaths：运行期产物的**目录约定**（相对路径的落点问题）。

   sessions_dir（ai.AI 默认 "sessions"）与 memory.DEFAULT_ROOT（"memory"）

   都是相对路径，落点是进程 CWD——从仓库根启动时会创建仓库根的 sessions/

   与 memory/。AppPaths 把这一约定显式化；main.py 从仓库根启动时把 CWD

   统一到程序目录（既有行为），目录自然落对位置。



2. ensure_core_sections()：对**用户旧 config.yaml** 缺失

   mode / modes / permissions / memory 四节的情况做注释保持式补齐，

   并打印可读提示——不允许静默丢失设置（02-architecture.md §4：

   update_yaml_scalars 在父块缺失时会静默跳过该键，红线 5.2 的直接推论）。



3. build_app()：以 AppPaths 为中心把目录约定汇总成一份组合根视图，

   供第二期渠道层（castorice/channels/）与外部装配方引用。真正的部件装配

   入口仍是 ai.AI（组合根视图见 AI.app 属性，t3 已冻结读法）。



硬性约束（红线 D）：本模块不得 import ui / PySide6 / ai；只依赖标准库。

"""



from __future__ import annotations


from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "AppPaths",
    "CORE_SECTIONS",
    "build_app",
    "build_default_paths",
    "ensure_core_sections",
]


# ----------------------------------------------------------------------------
# 运行期目录约定（docs/refactor/05-channel-contract.md 的数据落盘部分）
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class AppPaths:
    """运行期产物目录约定（相对路径均相对**进程 CWD** 解析）。

    frozen 保证装配方之间不会互相改写约定。
    """

    #: 会话事件日志根（sessions/<mode>/<session_id>.jsonl，按模式隔离）。
    #: ai.AI(sessions_dir=...) 的默认值与 SessionStore 的落盘根一致。
    sessions: Path
    #: 对话记忆数据库（ChromaDB 与文本层；既有 database/）。
    database: Path
    #: wiki / summary / worldbook 落盘根（castorice.memory.DEFAULT_ROOT）。
    memory: Path
    #: 运行日志（logs/app.log）与超长工具结果（logs/tool_results/）。
    logs: Path
    #: 角色设定分层（prompts/ 与 prompts/modes/）。
    prompts: Path
    #: 用户配置（config.yaml，注释保持写回）。
    config: Path

    def to_dict(self) -> Dict[str, str]:
        """渠道层 / 外部装配方消费的纯字符串视图（Path 不出内核）。"""
        return {
            "sessions": str(self.sessions),
            "database": str(self.database),
            "memory": str(self.memory),
            "logs": str(self.logs),
            "prompts": str(self.prompts),
            "config": str(self.config),
        }


def build_default_paths(root: Optional[Path] = None) -> AppPaths:
    """按程序根目录推导默认路径（root=None 时用进程 CWD，等价既有行为）。

    相对目录名与 ai.AI / castorice.memory 的内置默认保持一字不差：
    sessions / database / memory / logs / prompts / config.yaml。
    """
    base = Path(root) if root is not None else Path.cwd()
    return AppPaths(
        sessions=base / "sessions",
        database=base / "database",
        memory=base / "memory",
        logs=base / "logs",
        prompts=base / "prompts",
        config=base / "config.yaml",
    )


# ----------------------------------------------------------------------------
# 旧 config.yaml 的四节补齐（不静默丢失设置）
# ----------------------------------------------------------------------------

#: 第一期要求预置的配置节（example_config.yaml 已预置；02-architecture §4）。
CORE_SECTIONS: Tuple[str, ...] = ("mode", "modes", "permissions", "memory")

#: 缺失节的补齐内容——与 example_config.yaml 保持语义一致（注释随节写入）。
_SECTION_TEMPLATES: Dict[str, str] = {
    "mode": (
        '# 当前会话模式\n'
        'mode: "chat"                 # 可选: chat | work | code | learn\n'
    ),
    "modes": (
        '# 各模式的工作目录绑定（Code=可信代码目录，Learn=Obsidian Vault）\n'
        '# 留空表示未绑定；登记的目录同时是「可信根」，越界会被拒绝。\n'
        'modes:\n'
        '  work:\n'
        '    workdir: ""            # 工作模式的目录（可选）\n'
        '  code:\n'
        '    workdir: ""            # 编程模式的可信代码目录\n'
        '    sandbox: "workspace-write"\n'
        '  learn:\n'
        '    vault: ""              # 学习模式的 Obsidian Vault 根目录\n'
        '    sandbox: "workspace-write"\n'
    ),
    "permissions": (
        '# 权限与审批\n'
        'permissions:\n'
        '  sandbox_default: "read-only"   # read-only | workspace-write | danger-full-access\n'
        '  approval_policy: "ask"         # ask | never\n'
        '  command_allowlist: []          # 追加到内置只读命令白名单的命令\n'
    ),
    "memory": (
        '# 记忆引擎\n'
        'memory:\n'
        '  engine: "embedding"            # embedding | wiki | summary\n'
        '  summary_threshold: 40          # 累积多少条后压缩\n'
        '  worldbook_enabled: true\n'
    ),
}


def missing_core_sections(config: Dict[str, Any]) -> List[str]:
    """返回 config 中缺失的第一期四节（顺序与 CORE_SECTIONS 一致）。

    「缺失」指键不存在或值为 None（例如写了节名但没写内容的半成品，
    yaml 会解析成 None）；空 dict 视为存在——用户可能有意留空绑定。
    """
    missing: List[str] = []
    for name in CORE_SECTIONS:
        if config.get(name) is None:
            missing.append(name)
    return missing


def ensure_core_sections(
    config_path: str,
    *,
    log: Any = print,
) -> List[str]:
    """把旧 config.yaml 缺失的四节按模板补齐（注释保持式追加）。

    行为：
    * 文件不存在 -> 返回 []（首次启动由 main.py 用 example_config.yaml
      生成完整配置，不需要这里兜底）；
    * 解析失败 -> 打印可读提示并返回 []（**不改写**坏文件，避免抹掉
      用户手工修改；解析失败时 ai.ConfigManager 侧已有启动错误提示）；
    * 四节齐全 -> 不动文件；
    * 缺节 -> 在文件末尾追加缺失节的模板，并打印逐节提示。

    只追加、不重排：ConfigManager 的写回语义（update_yaml_scalars）要求
    注释与排版保持，这里同样不触碰已有内容。

    Returns:
        实际补齐的节名列表（已齐全或未改写时为空）。
    """
    path = Path(config_path)
    if not path.exists():
        return []

    try:
        import yaml

        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:  # 解析失败不改写（main.py 的启动错误提示已覆盖）
        log(f"提示: config.yaml 解析失败（{e}），跳过配置节检查；请先修复文件格式")
        return []

    if not isinstance(config, dict):
        log("提示: config.yaml 顶层不是映射，跳过配置节检查")
        return []

    missing = missing_core_sections(config)
    if not missing:
        return []

    text = path.read_text(encoding="utf-8")
    if text and not text.endswith("\n"):
        text += "\n"
    for name in missing:
        template = _SECTION_TEMPLATES.get(name, "").rstrip("\n")
        text += f"\n# ---- 以下由程序自动补齐（原配置缺少 {name} 节）----\n"
        text += template + "\n"
    path.write_text(text, encoding="utf-8")

    for name in missing:
        log(f"提示: config.yaml 缺少 {name} 节，已按默认值补齐"
            "（详见 example_config.yaml 的注释说明）")
    return missing


# ----------------------------------------------------------------------------
# 组合根视图
# ----------------------------------------------------------------------------

def build_app(root: Optional[Path] = None,
              config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """第一期组合根视图：目录约定 + 装配入口说明。

    第二期接入飞书 / 微信 iLink / QQ 渠道时，渠道适配层只依赖这里的
    AppPaths（sessions 落盘位置）与 ai.AI 的冻结接口
    （get_response_stream / set_mode / set_approval_responder 等），
    **不需要改动 castorice/harness.py 的核心签名**。

    config 当前只用于校验四节（缺节时给出可读告警，不改写文件——
    文件改写走 ensure_core_sections，由 main.py 在启动早期调用）。
    """
    paths = build_default_paths(root)
    warnings: List[str] = []
    if config:
        for name in missing_core_sections(config):
            warnings.append(
                f"config.yaml 缺少 {name} 节，将按代码内默认值运行"
                "（mode=chat / sandbox=read-only / approval=ask / engine=embedding）")
    return {"paths": paths, "warnings": warnings}
