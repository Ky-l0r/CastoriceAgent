"""CastoriceAgent 内核包（四模式 / 会话事件日志 / agent 循环）。

本包是 DeepSeek Harness（DSH）范式在 Python/PySide6 环境的落点，
对应 `docs/refactor/02-architecture.md` §1 的分层设计。

硬性约束（红线 D）：
1. 本包**不得** import ui / PySide6 / ai；
2. 本包第一期只依赖 Python 标准库，保证可在无 Qt、无 API Key 的环境下被测试驱动；
3. 依赖方向是 ai.py → castorice/，绝不反向。
"""

from .harness import (
    DEFAULT_RESULT_LIMIT,
    MAX_ITERATIONS_NOTE,
    HarnessLoop,
    PermissionBridge,
    PermissionDecision,
    PermissionRequest,
    RepeatReminder,
    StepRecord,
    StopReason,
    ToolCall,
    TurnOutcome,
)
from .modes import (
    ALL_TOOLS,
    BUILTIN_MODES,
    CODE,
    CHAT,
    LEARN,
    PLANNED_TOOL_NAMES,
    SANDBOX_DANGER_FULL_ACCESS,
    SANDBOX_MODES,
    SANDBOX_READ_ONLY,
    SANDBOX_WORKSPACE_WRITE,
    WORK,
    Mode,
    ModeRegistry,
    ToolPolicy,
    ToolSpec,
    WorkdirError,
    WorkdirPolicy,
    build_harness_registry,
    builtin_modes,
    coerce_tool,
    resolve_mode_name,
)
from .session import (
    FORMAT_VERSION,
    KIND_ASSISTANT,
    KIND_CHECKPOINT,
    KIND_CONTEXT,
    KIND_MODE,
    KIND_PROACTIVE,
    KIND_SANDBOX,
    KIND_STOP,
    KIND_TOOL_CALL,
    KIND_TOOL_RESULT,
    KIND_USER,
    TOOL_OUTCOME_UNKNOWN_TEXT,
    CheckpointFailed,
    SessionError,
    SessionEvent,
    SessionFormatError,
    SessionHeader,
    SessionInUseError,
    SessionProjection,
    SessionStore,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # modes
    "ALL_TOOLS", "BUILTIN_MODES", "CHAT", "CODE", "LEARN", "WORK",
    "PLANNED_TOOL_NAMES",
    "SANDBOX_DANGER_FULL_ACCESS", "SANDBOX_MODES", "SANDBOX_READ_ONLY",
    "SANDBOX_WORKSPACE_WRITE",
    "Mode", "ModeRegistry", "ToolPolicy", "ToolSpec",
    "WorkdirError", "WorkdirPolicy",
    "build_harness_registry", "builtin_modes", "coerce_tool", "resolve_mode_name",
    # session
    "FORMAT_VERSION",
    "KIND_ASSISTANT", "KIND_CHECKPOINT", "KIND_CONTEXT", "KIND_MODE",
    "KIND_PROACTIVE", "KIND_SANDBOX", "KIND_STOP", "KIND_TOOL_CALL",
    "KIND_TOOL_RESULT", "KIND_USER", "TOOL_OUTCOME_UNKNOWN_TEXT",
    "CheckpointFailed", "SessionError", "SessionEvent", "SessionFormatError",
    "SessionHeader", "SessionInUseError", "SessionProjection", "SessionStore",
    # harness
    "DEFAULT_RESULT_LIMIT", "MAX_ITERATIONS_NOTE", "HarnessLoop",
    "PermissionBridge", "PermissionDecision", "PermissionRequest",
    "RepeatReminder", "StepRecord", "StopReason",
    "ToolCall", "TurnOutcome",
]
