"""受限命令执行（对齐 dsh-tool-bash 的策略级降级与 P22 / P23）。

三条硬性语义（01-gap-matrix.md 第 3 节）：

1. **默认拒绝**：命令只有在已授权模式（Work / Code，其
   WorkdirPolicy.command_allowlist 非空）里、且命中白名单时才执行；
   Learn 的白名单为空 -> 一律 COMMAND_MODE_DENIED。
2. **绝不用 shell**：subprocess.run(shell=False, ...)，参数走列表；
   显式拒绝表覆盖 rm / del / Remove-Item / pip install / curl / powershell 等。
3. **非零退出码是结构化结果，不是异常**：返回码、耗时、截断后的
   stdout/stderr 一起作为普通工具结果返回，轮次继续。

超时（P22）与输出长度上限（P20）都由这里强制；环境变量注入 ASTER_ 前缀的
托管变量（P23 / R14），让受限命令能读到会话 id、模式、工作区与沙箱。
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .base import (
    TOOL_KIND_COMMAND,
    ToolContext,
    ToolSpec,
    error_text,
    truncate_text,
)
from ..permissions import (
    CommandDenied,
    CommandPolicy,
    command_argv,
)

#: 默认超时（秒）与输出上限（字符）
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_OUTPUT_LIMIT = 12_000

#: 托管环境变量前缀（P23 / R14）：受限命令里能读到会话上下文
ENV_PREFIX = "ASTER_"
ENV_KEYS = (
    "ASTER_SESSION_ID",
    "ASTER_MODE",
    "ASTER_WORKSPACE",
    "ASTER_SANDBOX",
)


def build_env(context: ToolContext) -> Dict[str, str]:
    """构造子进程环境：继承 os.environ 并注入 ASTER_ 托管变量。"""
    env = dict(os.environ)
    env[ENV_PREFIX + "SESSION_ID"] = str(context.session_id or "")
    env[ENV_PREFIX + "MODE"] = str(context.mode or "")
    env[ENV_PREFIX + "WORKSPACE"] = str(context.effective_workspace or "")
    env[ENV_PREFIX + "SANDBOX"] = str(context.sandbox or "")
    # 让子进程里的输出编码稳定（Windows 默认可能是 GBK）
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def resolve_allowlist(context: ToolContext) -> Sequence[str]:
    """当前模式生效的命令白名单（来自网关；网关不可用时为空 = 一律拒绝）。"""
    gateway = context.gateway
    if gateway is None:
        return ()
    try:
        return gateway.command_allowlist(context.mode)
    except Exception:
        return ()


def run_argv(
    argv: Sequence[str],
    *,
    cwd: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    env: Optional[Mapping[str, str]] = None,
    output_limit: int = DEFAULT_OUTPUT_LIMIT,
    shell: bool = False,
) -> Dict[str, Any]:
    """执行一条命令，返回结构化结果（永不抛异常）。

    Returns:
        {"argv":..., "cwd":..., "exit_code": int|None, "timed_out": bool,
         "duration_s": float, "stdout": str, "stderr": str, "rendered": str}
    """
    started = time.monotonic()
    if shell:
        raise ValueError("run_argv 禁止 shell=True（策略级隔离的前提）")
    exit_code: Optional[int]
    timed_out = False
    try:
        completed = subprocess.run(
            [str(item) for item in argv],
            cwd=cwd or None,
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(0.1, float(timeout)),
            shell=False,
            check=False,
        )
        exit_code = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        exit_code = None
        stdout = _decode(exc.stdout)
        stderr = _decode(exc.stderr)
        timed_out = True
    except FileNotFoundError as exc:
        exit_code = None
        stdout = ""
        stderr = "找不到可执行文件: " + str(exc)
    except (OSError, ValueError) as exc:
        exit_code = None
        stdout = ""
        stderr = "无法执行命令: " + str(exc)

    duration = time.monotonic() - started
    payload: Dict[str, Any] = {
        "argv": [str(item) for item in argv],
        "cwd": cwd or "",
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_s": round(duration, 3),
        "stdout": truncate_text(stdout, output_limit,
                                note="…（标准输出过长，已省略中间部分）…"),
        "stderr": truncate_text(stderr, output_limit,
                                note="…（标准错误过长，已省略中间部分）…"),
    }
    payload["rendered"] = render_result(payload, timeout=timeout)
    return payload


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def render_result(payload: Mapping[str, Any],
                  *, timeout: float = DEFAULT_TIMEOUT_S) -> str:
    """把结构化结果渲染成给模型看的文本（明确区分超时与失败）。"""
    argv = " ".join(str(item) for item in (payload.get("argv") or []))
    if payload.get("timed_out"):
        head = "命令超时（超过 " + str(timeout) + " 秒已终止）: " + argv
    else:
        code = payload.get("exit_code")
        if code == 0 and not str(payload.get("stderr") or "").strip():
            head = "命令执行成功（退出码 0）: " + argv
        elif code == 0:
            head = "命令执行成功（退出码 0，但有 stderr 输出）: " + argv
        else:
            head = "命令执行失败（非零退出码 " + str(code) + "）: " + argv
    lines: List[str] = [
        head,
        "工作目录: " + str(payload.get("cwd") or ""),
        "耗时: " + str(payload.get("duration_s")) + " 秒",
    ]
    stdout = str(payload.get("stdout") or "").strip()
    stderr = str(payload.get("stderr") or "").strip()
    lines.append("标准输出:\n" + (stdout or "（空）"))
    lines.append("标准错误:\n" + (stderr or "（空）"))
    return "\n".join(lines)


def make_run_command(context: ToolContext):
    def run_command(command: Any = "", argv: Optional[Sequence[str]] = None,
                    timeout: float = DEFAULT_TIMEOUT_S,
                    cwd: Optional[str] = None) -> str:
        tokens = [str(item) for item in argv] \
            if isinstance(argv, (list, tuple)) and argv else command_argv({"command": command})
        if not tokens:
            return error_text("必须提供 command（字符串）或 argv（列表）", "BAD_ARGUMENTS")

        policy = CommandPolicy(allowlist=tuple(resolve_allowlist(context)))
        try:
            policy.check(tokens)
        except CommandDenied as exc:
            return error_text(str(exc), exc.code)

        workdir = cwd or context.cwd_or_workspace()
        try:
            workdir = context.check_read(workdir)
        except Exception as exc:
            return error_text("工作目录不可用: " + str(exc), "FS_SANDBOX_DENIED")
        if not os.path.isdir(workdir):
            return error_text("工作目录不存在: " + workdir, "FS_NOT_FOUND")

        result = run_argv(
            tokens,
            cwd=workdir,
            timeout=max(0.1, float(timeout or DEFAULT_TIMEOUT_S)),
            env=build_env(context),
        )
        return result["rendered"]

    return run_command


def _schema(properties: Mapping[str, Any], required: Sequence[str] = ()) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "type": "object",
        "properties": dict(properties),
        "additionalProperties": False,
    }
    if required:
        payload["required"] = list(required)
    return payload


def build_command_tools(context: ToolContext) -> List[ToolSpec]:
    """构造受限命令工具（只读子集，白名单驱动）。"""
    return [
        ToolSpec(
            name="run_command",
            description=(
                "在已授权的工作目录里执行白名单内的只读命令"
                "（dir/ls/type/cat/git status/git diff/python -m pytest 等）。"
                "默认拒绝一切命令；命令不在白名单时返回可读错误。"
                "非零退出码作为结构化结果返回，不算失败异常。"
            ),
            parameters=_schema({
                "command": {
                    "type": "string",
                    "description": "完整命令行（按空白切分，不走 shell 解析）",
                },
                "argv": {
                    "type": "array",
                    "description": "参数列表形式（优先于 command；更安全）",
                    "items": {"type": "string"},
                },
                "timeout": {"type": "number", "description": "超时秒数，默认 30"},
                "cwd": {"type": "string", "description": "工作目录，默认当前工作区"},
            }),
            handler=make_run_command(context),
            read_only=False,
            requires_approval=True,
            parallel_safe=False,
            timeout_s=DEFAULT_TIMEOUT_S,
            kind=TOOL_KIND_COMMAND,
        ),
    ]
