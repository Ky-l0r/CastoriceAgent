"""主动聊天调度器（第二期 t4）：schedules.json + 双投递 + 两条 DSH 硬约定。

对标 dsh-schedule 的两条硬约定（01-gap-matrix.md §9，t7 会构造反例验证）：

约定①  **只补发最近一次错过的时点**——程序停了一周后启动，一个每日提醒
        错过了 7 个时点，只补发最近的那 1 条，不刷屏。
约定②  **投递在 flush 之后才提交**——先召回冷会话并触发 harness 轮次的
        检查点（内部 flush），flush 成功后才执行投递；崩溃后重启不会
        重复发（要么上一轮已落盘「已投递」，要么根本没投递）。

落盘约定（02-architecture.md Q4）：
* `schedules.json` 放**程序目录**（与 ui_settings.json 同级），不进发布包
  （打包脚本按白名单复制，本文件天然不会进包；docs/refactor/06-scheduler.md
  有发布包核查条目）。
* 时钟只依赖本地系统时间（datetime.now()）；调度精度为**分钟级**——
  triggers 的时点以分钟对齐，秒字段被忽略。

触发链路：到期 -> 召回/新建 chat 冷会话 -> harness.run_stream(合成触发语，
source="schedule") -> harness 轮次内部完成 kind=proactive 的 user 事件落盘
与检查点 -> flush 成功 -> 双投递（按 target_id 前缀路由：桌面走
DesktopNotifier 协议，QQ 走渠道 send）。

红线 D：本模块不 import ui / PySide6 / ai；Qt 导入只发生在
SystemTrayNotifier 运行期方法内（castorice.notify 延迟加载）。
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

from .channels.base import ChannelError, OutboundMessage, normalize_target
from .notify import DeliveryResult, DesktopNotifier

__all__ = [
    "MAX_TITLE_LEN",
    "SCHEDULE_KINDS",
    "ScheduleError",
    "ScheduleSpec",
    "ScheduleStore",
    "Scheduler",
    "SchedulerConfig",
    "SchedulerTarget",
    "previous_occurrence",
]

logger = logging.getLogger(__name__)

#: title 上限（对齐 dsh-schedule：title 必填且不超过 120 字符）
MAX_TITLE_LEN = 120

#: 五选一的触发器（互斥，对齐 dsh-schedule 的 after_seconds / at /
#: every_seconds / daily / weekly；cron 不在第一期范围）
SCHEDULE_KINDS = ("after_seconds", "at", "every_seconds", "daily", "weekly")


class ScheduleError(RuntimeError):
    """schedules.json / ScheduleSpec 校验或存取失败。"""


# ----------------------------------------------------------------------------
# 时点推算（纯函数，便于单测）
# ----------------------------------------------------------------------------

def previous_occurrence(kind: str, spec: Dict[str, Any], now: datetime,
                        *, first_at: Optional[datetime] = None) -> Optional[datetime]:
    """返回 kind/spec 定义的时刻序列中 <= now 的最近一次发生时点。

    返回 None 表示「尚无任何发生时点」（周期起点还未到）——这种情况下
    不补发（约定①：补发的是*错过的*时点，未来时点不算）。

    * after_seconds：一次性。first_at（创建基准）+ after_seconds 秒；
      已过期即该唯一时点，未过期返回 None。
    * at：一次性。ISO 字面量（本地时间）；已过期返回该时点。
    * every_seconds：固定周期。起点 first_at，间隔 interval；时点为
      first_at + n*interval 中 <= now 的最大者。无起点返回 None。
    * daily：每天 HH:MM。分钟对齐（秒字段忽略）。
    * weekly：每周 (weekday, HH:MM)。weekday 与 Python 一致（0=周一..6=周日）。
    """
    minute = now.replace(second=0, microsecond=0)

    def _parse_hhmm(value: Any) -> Tuple[int, int]:
        text = str(value or "").strip()
        parts = text.split(":")
        if len(parts) != 2:
            raise ScheduleError(f"时间必须是 HH:MM，得到 {value!r}")
        try:
            hh, mm = int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise ScheduleError(f"时间必须是 HH:MM，得到 {value!r}") from exc
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ScheduleError(f"时间超出范围: {value!r}")
        return hh, mm

    if kind == "after_seconds":
        if first_at is None:
            return None
        seconds = float(spec.get("after_seconds") or 0)
        due = first_at + timedelta(seconds=seconds)
        return due if due <= now else None

    if kind == "at":
        raw = str(spec.get("at") or "").strip()
        try:
            due = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ScheduleError(f"at 必须是 ISO 时间字面量，得到 {raw!r}") from exc
        return due if due <= now else None

    if kind == "every_seconds":
        if first_at is None:
            return None
        interval = float(spec.get("every_seconds") or 0)
        if interval <= 0:
            raise ScheduleError("every_seconds 必须为正数")
        elapsed = (now - first_at).total_seconds()
        if elapsed < 0:
            return None
        n = int(elapsed // interval)
        due = first_at + timedelta(seconds=n * interval)
        return due if due <= now else None

    if kind == "daily":
        hh, mm = _parse_hhmm(spec.get("daily"))
        candidate = minute.replace(hour=hh, minute=mm)
        if candidate > minute:
            candidate = candidate - timedelta(days=1)
        if first_at is not None:
            floor = first_at.replace(second=0, microsecond=0)
            if candidate < floor and candidate.date() == floor.date():
                return None
            if candidate.date() < floor.date():
                return None
        return candidate

    if kind == "weekly":
        weekday = spec.get("weekday")
        if weekday is None:
            raise ScheduleError("weekly 触发器缺少 weekday（0=周一..6=周日）")
        weekday = int(weekday)
        if not (0 <= weekday <= 6):
            raise ScheduleError(f"weekday 超出范围: {weekday}")
        hh, mm = _parse_hhmm(spec.get("weekly"))
        candidate = minute.replace(hour=hh, minute=mm)
        delta_days = (candidate.weekday() - weekday) % 7
        candidate = candidate - timedelta(days=delta_days)
        if candidate > minute:
            candidate = candidate - timedelta(days=7)
        if first_at is not None:
            floor = first_at.replace(second=0, microsecond=0)
            if candidate < floor:
                first_possible = floor + timedelta(
                    days=(floor.weekday() - weekday) % 7)
                first_possible = first_possible.replace(hour=hh, minute=mm)
                if first_possible > now:
                    return None
        return candidate

    raise ScheduleError(f"未知触发器类型: {kind!r}")


def _next_occurrence(kind: str, spec: Dict[str, Any], now: datetime,
                     first_at: Optional[datetime]) -> Optional[datetime]:
    """返回 > now 的下一次发生时点（内部计算，供 sleep / 展示用）。"""
    try:
        if kind == "after_seconds":
            if first_at is None:
                return None
            due = first_at + timedelta(seconds=float(spec.get("after_seconds") or 0))
            return due if due > now else None
        if kind == "at":
            try:
                due = datetime.fromisoformat(str(spec.get("at") or ""))
            except ValueError:
                return None
            return due if due > now else None
        if kind == "every_seconds":
            if first_at is None:
                return None
            interval = float(spec.get("every_seconds") or 0)
            if interval <= 0:
                return None
            elapsed = (now - first_at).total_seconds()
            n = int(elapsed // interval) + 1
            return first_at + timedelta(seconds=n * interval)
        if kind == "daily":
            parts = str(spec.get("daily") or "").split(":")
            hh, mm = int(parts[0]), int(parts[1])
            candidate = now.replace(second=0, microsecond=0, hour=hh, minute=mm)
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate
        if kind == "weekly":
            weekday = int(spec.get("weekday"))
            parts = str(spec.get("weekly") or "").split(":")
            hh, mm = int(parts[0]), int(parts[1])
            candidate = now.replace(second=0, microsecond=0, hour=hh, minute=mm)
            delta_days = (candidate.weekday() - weekday) % 7
            candidate = candidate - timedelta(days=delta_days)
            if candidate <= now:
                candidate += timedelta(days=7)
            return candidate
    except (ValueError, TypeError):
        return None
    return None


# ----------------------------------------------------------------------------
# 数据模型
# ----------------------------------------------------------------------------

@dataclass
class SchedulerTarget:
    """一个投递目标。

    id 形如 "desktop:main" / "qq:12345678"（前缀路由投递实现）；QQ 目标
    的 chat 部分会被 normalize_target 校验非空。
    """

    id: str
    channel: str = "desktop"     # "desktop" | "qq"
    fallback: str = "skip"       # 渠道离线时策略："desktop" | "skip"

    def validate(self) -> None:
        raw = str(self.id or "").strip()
        if not raw:
            raise ScheduleError("target.id 不能为空")
        if ":" in raw:
            self.channel, _, rest = raw.partition(":")
        else:
            rest = raw
        rest = rest.strip()
        self.id = self.channel + ":" + rest
        if self.channel == "qq":
            # QQ 目标必须能过渠道层的 normalize_target（非空校验）
            normalize_target(rest)
        elif self.channel != "desktop":
            raise ScheduleError(f"未知 target 渠道: {self.channel!r}")
        if self.fallback not in ("desktop", "skip"):
            raise ScheduleError(
                f"target.fallback 必须是 desktop|skip，得到 {self.fallback!r}")


@dataclass
class ScheduleSpec:
    """一条定时任务（schedules.json 的一条）。

    五选一触发器（互斥）：after_seconds / at / every_seconds / daily / weekly。
    targets 至少一个；proactive_message 是触发时交给模型的**合成主动话头**
    （ProactiveTrigger 的载体）——不是用户原话，恢复会话时靠
    SessionEvent.kind='proactive' 与真实用户输入区分。
    """

    id: str
    title: str
    triggers: Dict[str, Any]
    proactive_message: str
    targets: List[SchedulerTarget] = field(default_factory=list)
    enabled: bool = True
    #: 注册基准（every_seconds 的周期起点 / after_seconds 的到期基准 /
    #: daily / weekly 的最早起点）；ISO 字面量。存盘时保留，保证重启后
    #: 周期相位不变，也杜绝「注册前就存在的时点被历史性补发」。
    first_at: Optional[str] = None
    #: 最近一次已投递的发生时点（ISO 字面量；本地时间）。
    last_sent_at: Optional[str] = None

    def validate(self) -> None:
        if not str(self.id or "").strip():
            raise ScheduleError("schedule.id 不能为空")
        title = str(self.title or "").strip()
        if not title:
            raise ScheduleError("schedule.title 必填")
        if len(title) > MAX_TITLE_LEN:
            raise ScheduleError(
                f"schedule.title 超过 {MAX_TITLE_LEN} 字符: {len(title)}")
        if not isinstance(self.triggers, dict) or not self.triggers:
            raise ScheduleError("schedule.triggers 必须是非空映射")
        selected = [k for k in SCHEDULE_KINDS if k in self.triggers]
        if len(selected) != 1:
            raise ScheduleError(
                "触发器五选一（互斥），实际有 " + repr(selected)
                + "；未知键: "
                + repr([k for k in self.triggers if k not in SCHEDULE_KINDS]))
        kind = selected[0]
        spec_value = self.triggers[kind]
        if kind in ("after_seconds", "every_seconds"):
            value = float(spec_value)
            if value <= 0:
                raise ScheduleError(f"{kind} 必须为正数")
        elif kind == "at":
            try:
                datetime.fromisoformat(str(spec_value))
            except ValueError as exc:
                raise ScheduleError(f"at 必须是 ISO 时间字面量: {spec_value!r}") from exc
        elif kind == "daily":
            previous_occurrence("daily", {"daily": spec_value}, datetime.now())
        elif kind == "weekly":
            if "weekday" not in self.triggers:
                raise ScheduleError("weekly 触发器缺少 weekday（0=周一..6=周日）")
            previous_occurrence(
                "weekly",
                {"weekly": spec_value, "weekday": self.triggers.get("weekday")},
                datetime.now())
        if not self.targets:
            raise ScheduleError("schedule.targets 至少一个")
        for target in self.targets:
            target.validate()
        if not str(self.proactive_message or "").strip():
            raise ScheduleError("schedule.proactive_message 必填（合成主动话头）")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "triggers": dict(self.triggers),
            "proactive_message": self.proactive_message,
            "targets": [
                {"id": t.id, "channel": t.channel, "fallback": t.fallback}
                for t in self.targets
            ],
            "enabled": bool(self.enabled),
            "first_at": self.first_at,
            "last_sent_at": self.last_sent_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScheduleSpec":
        if not isinstance(data, dict):
            raise ScheduleError("schedule 条目必须是映射")
        targets_raw = data.get("targets") or []
        if not isinstance(targets_raw, (list, tuple)):
            raise ScheduleError("schedule.targets 必须是列表")
        targets = [
            SchedulerTarget(
                id=str(t.get("id") or ""),
                channel=str(t.get("channel") or "desktop"),
                fallback=str(t.get("fallback") or "skip"),
            )
            for t in targets_raw if isinstance(t, dict)
        ]
        return cls(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or ""),
            triggers=dict(data.get("triggers") or {}),
            proactive_message=str(data.get("proactive_message") or ""),
            targets=targets,
            enabled=bool(data.get("enabled", True)),
            first_at=data.get("first_at") or None,
            last_sent_at=data.get("last_sent_at") or None,
        )

    def trigger_kind(self) -> str:
        selected = [k for k in SCHEDULE_KINDS if k in self.triggers]
        if len(selected) != 1:
            raise ScheduleError("触发器五选一（互斥），实际有 " + repr(selected))
        return selected[0]

    def trigger_spec(self) -> Dict[str, Any]:
        kind = self.trigger_kind()
        spec: Dict[str, Any] = {kind: self.triggers[kind]}
        if kind == "weekly":
            spec["weekday"] = self.triggers.get("weekday")
        return spec

    def _first_at_dt(self) -> Optional[datetime]:
        if not self.first_at:
            return None
        try:
            return datetime.fromisoformat(self.first_at)
        except ValueError:
            return None

    def previous_due(self, now: datetime) -> Optional[datetime]:
        return previous_occurrence(
            self.trigger_kind(), self.trigger_spec(), now,
            first_at=self._first_at_dt())

    def next_due(self, now: datetime) -> Optional[datetime]:
        return _next_occurrence(
            self.trigger_kind(), self.trigger_spec(), now,
            first_at=self._first_at_dt())


# ----------------------------------------------------------------------------
# schedules.json 存取
# ----------------------------------------------------------------------------

SCHEMAS_VERSION = 1


class ScheduleStore:
    """schedules.json 的存取（程序目录，不进发布包）。

    坏文件策略（与 SessionFormatError 的 fail-fast 精神一致）：JSON 解析
    失败 / 顶层不是映射 / schedules 不是列表 -> 抛 ScheduleError，绝不
    静默清空用户的定时任务。写回走 .tmp + replace 原子替换。
    """

    def __init__(self, path: Any) -> None:
        self.path = Path(path)

    def load(self) -> List[ScheduleSpec]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ScheduleError(f"schedules.json 解析失败: {exc}") from exc
        if not isinstance(data, dict):
            raise ScheduleError("schedules.json 顶层必须是映射")
        items = data.get("schedules")
        if items is None:
            items = []
        if not isinstance(items, list):
            raise ScheduleError("schedules.json 的 schedules 必须是列表")
        specs: List[ScheduleSpec] = []
        for item in items:
            spec = ScheduleSpec.from_dict(item)
            spec.validate()
            specs.append(spec)
        return specs

    def save(self, specs: Sequence[ScheduleSpec]) -> None:
        for spec in specs:
            spec.validate()
        data = {
            "version": SCHEMAS_VERSION,
            "schedules": [s.to_dict() for s in specs],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        tmp.replace(self.path)


# ----------------------------------------------------------------------------
# agent 侧协议 / 触发记录
# ----------------------------------------------------------------------------

class AgentProxy(Protocol):
    """调度器对 agent 侧的最小依赖（鸭子类型，避免 import ai）。

    bot 是 ai.AI 实例：调度器取它的 harness（HarnessLoop）触发
    source='schedule' 的轮次；新建冷会话走 new_chat_session()。
    """

    def get_response_stream(self, user_input: str, images=None):  # pragma: no cover
        ...

    def new_chat_session(self):  # pragma: no cover
        """清空上下文并新建 chat 会话（ai.AI 既有方法）。"""
        ...


class ProactiveResult:
    """一次主动触发的执行记录（测试与日志消费）。"""

    def __init__(self, schedule_id: str, due: datetime, ok: bool,
                 reply: str = "", deliveries: int = 0,
                 error: str = "") -> None:
        self.schedule_id = schedule_id
        self.due = due
        self.ok = ok
        self.reply = reply
        self.deliveries = deliveries
        self.error = error

    def __repr__(self) -> str:  # pragma: no cover - 调试便利
        return (f"ProactiveResult(schedule={self.schedule_id!r}, ok={self.ok}, "
                f"deliveries={self.deliveries}, error={self.error!r})")


# ----------------------------------------------------------------------------
# 调度器
# ----------------------------------------------------------------------------

@dataclass
class SchedulerConfig:
    """调度器可配置项（来自 config.yaml 或装配方注入）。"""

    check_interval: float = 10.0        # 后台线程的轮询间隔（秒）
    #: 未识别渠道 target 的行为：skip（跳过并记日志）| desktop（回退桌面）
    offline_fallback: str = "skip"

    def validate(self) -> None:
        if self.offline_fallback not in ("skip", "desktop"):
            raise ScheduleError(
                f"offline_fallback 必须是 skip|desktop，得到 {self.offline_fallback!r}")


class Scheduler:
    """主动聊天调度器（双投递 + 两条 DSH 硬约定）。

    用法::

        store = ScheduleStore(Path("schedules.json"))
        scheduler = Scheduler(
            bot=ai,                        # 提供 harness 与 new_chat_session
            store=store,
            desktop=SystemTrayNotifier(),  # DesktopNotifier 协议
            channels={"qq": napcat},       # 名称 -> Channel（有 send）
            config=SchedulerConfig(check_interval=10),
        )
        scheduler.start()    # 后台线程轮询（幂等）
        ...
        scheduler.run_due()  # 手动驱动（测试即用这个）
        scheduler.close()    # 幂等

    投递路由：target_id 形如 "qq:12345" 走 channels["qq"].send()；
    "desktop:main" 走 desktop.notify()。QQ 投递失败 / 渠道离线时按
    target.fallback：'desktop' 回退桌面通知，'skip' 跳过并记日志。
    """

    def __init__(
        self,
        bot: Any,
        store: ScheduleStore,
        desktop: Optional[DesktopNotifier] = None,
        channels: Optional[Dict[str, Any]] = None,
        config: Optional[SchedulerConfig] = None,
        logger: Any = None,
    ) -> None:
        self.bot = bot
        self.store = store
        self.desktop = desktop
        self.channels: Dict[str, Any] = dict(channels or {})
        self.config = config or SchedulerConfig()
        self.config.validate()
        self.logger = logger or logging.getLogger(__name__)
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ---- 生命周期 ----

    def start(self) -> None:
        """启动后台轮询线程（幂等）。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run_loop, name="proactive-scheduler", daemon=True)
            self._thread.start()

    def close(self) -> None:
        """停止后台线程；幂等。"""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() \
                and thread is not threading.current_thread():
            thread.join(timeout=self.config.check_interval + 5.0)
        self._thread = None

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_due()
            except Exception as exc:  # pragma: no cover - 轮询容错
                self.logger.error("主动调度轮询异常: %s", exc)
            self._stop.wait(self.config.check_interval)

    # ---- 到期判定与补发（约定①） ----

    def run_due(self, now: Optional[datetime] = None) -> List[ProactiveResult]:
        """检查所有任务，触发到期的那些；返回触发记录。

        约定①：对每个任务只取 `previous_due(now)` 的**最近一个**时点，
        且仅当它晚于 last_sent_at（没发过）时才补发——错过 3 个时点
        也只发最近 1 条。未来时点不触发。投递成功才推进 last_sent_at；
        失败保留原值，下一轮还能重试同一时点。
        """
        now = now or datetime.now()
        results: List[ProactiveResult] = []
        with self._lock:
            specs = self.store.load()
            changed = False
            for spec in specs:
                if not spec.enabled:
                    continue
                due = spec.previous_due(now)
                if due is None:
                    continue
                last_sent = _parse_iso(spec.last_sent_at)
                if last_sent is not None and due <= last_sent:
                    continue  # 该时点已投递过
                result = self._fire(spec, due)
                results.append(result)
                if result.ok:
                    spec.last_sent_at = _iso(due)
                    changed = True
            if changed:
                self.store.save(specs)
        return results

    # ---- 单次触发 ----

    def _fire(self, spec: ScheduleSpec, due: datetime) -> ProactiveResult:
        """触发一个到期任务：召回会话 -> run_stream -> flush 后投递。"""
        try:
            self._ensure_session(spec)
        except Exception as exc:
            return ProactiveResult(spec.id, due, ok=False,
                                   error=f"召回会话失败: {exc}")
        harness = getattr(self.bot, "harness", None)
        if harness is None:
            return ProactiveResult(spec.id, due, ok=False,
                                   error="bot 未提供 harness 属性")

        trigger_text = _build_trigger_text(spec, due)
        reply = ""
        try:
            # harness 的合成触发标记是 source="proactive"（harness.py 的
            # run_stream 判定位；t3 冻结签名）：会话日志里以 kind=proactive
            # 记录合成触发语（与真实用户输入可区分）；轮次内部还有
            # pre-step / pre-request 语义检查点 flush。
            # 注：任务书写的 source='schedule' 与内核既有的 "proactive"
            # 判定值对齐——语义一致，取内核既有值，不改 harness 签名。
            for kind, payload in harness.run_stream(trigger_text,
                                                    source="proactive"):
                if kind == "text":
                    reply += str(payload)
                elif kind == "done":
                    reply = str(payload)
                    break
        except Exception as exc:
            return ProactiveResult(spec.id, due, ok=False,
                                   error=f"agent 轮次失败: {exc}")

        # ---- 约定②：投递前强制 flush（fail-closed）----
        session = getattr(harness, "session", None)
        if session is not None:
            try:
                session.flush(reason="proactive-delivery")
            except Exception as exc:
                self.logger.error(
                    "主动消息 flush 失败，放弃投递（约定②）: %s", exc)
                return ProactiveResult(spec.id, due, ok=False,
                                       error=f"flush 失败: {exc}")

        count, skipped_by_policy = self._deliver(spec, due, reply)
        # 全部目标投递失败且没有一个是「skip 策略下的正常放弃」->
        # 整次触发记为失败：run_due 不推进 last_sent_at，下一轮重试
        # 同一时点（与约定①的自愈语义一致）。skip 策略下的 0 投递是
        # 配置允许的正常结果（已记日志），不算失败、不重试。
        if count == 0 and not skipped_by_policy:
            return ProactiveResult(spec.id, due, ok=False, reply=reply,
                                   deliveries=0, error="所有目标投递失败")
        return ProactiveResult(spec.id, due, ok=True, reply=reply,
                               deliveries=count)

    def _ensure_session(self, spec: ScheduleSpec) -> None:
        """召回/新建冷会话：主动消息永远走全新 chat 会话，不打断用户
        当前 UI 会话（ai.AI.new_chat_session 清空上下文并新建落盘会话）。"""
        new_session = getattr(self.bot, "new_chat_session", None)
        if callable(new_session):
            new_session()

    # ---- 双投递 ----

    def _deliver(self, spec: ScheduleSpec, due: datetime, reply: str
                 ) -> Tuple[int, bool]:
        """把 reply 投递到 spec.targets 的每个目标。

        返回 (成功数, skipped_by_policy)：skipped_by_policy=True 表示至少
        有一个未成功目标是 skip 策略下的正常放弃（已记日志），此时 0 投递
        不算触发失败。"""
        delivered = 0
        skipped_by_policy = False
        title = spec.title
        body = reply or spec.proactive_message
        for target in spec.targets:
            ok, by_policy = self._deliver_one(target, title, body)
            if ok:
                delivered += 1
            elif by_policy:
                skipped_by_policy = True
        return delivered, skipped_by_policy

    def _deliver_one(self, target: SchedulerTarget, title: str,
                     body: str) -> Tuple[bool, bool]:
        """投递单个目标；返回 (是否成功, 是否 skip 策略下的正常放弃)。"""
        channel, _, _chat_part = target.id.partition(":")
        if channel == "qq":
            return self._deliver_qq(target, title, body)
        if channel == "desktop":
            return self._deliver_desktop(target, title, body), False
        # 未识别渠道：按全局配置降级
        if self.config.offline_fallback == "desktop" and self.desktop is not None:
            self.logger.warning(
                "未识别渠道 %r，回退桌面投递: %s", channel, target.id)
            return self._deliver_desktop(target, title, body), False
        self.logger.warning("未识别渠道 %r，跳过投递: %s", channel, target.id)
        return False, True   # skip 并记日志：策略性放弃

    def _deliver_desktop(self, target: SchedulerTarget, title: str,
                         body: str) -> bool:
        if self.desktop is None:
            self.logger.warning("桌面通知实现未注册，跳过投递: %s", target.id)
            return False
        try:
            result = self.desktop.notify(title, body, target_id=target.id)
        except Exception as exc:
            self.logger.error("桌面投递异常 %s: %s", target.id, exc)
            return False
        if result.ok:
            self.logger.info("桌面投递成功: %s", target.id)
            return True
        self.logger.warning("桌面投递失败 %s: %s", target.id, result.detail)
        return False

    def _deliver_qq(self, target: SchedulerTarget, title: str,
                    body: str) -> Tuple[bool, bool]:
        chat_part = target.id.split(":", 1)[1] if ":" in target.id else ""
        try:
            chat_id = normalize_target(chat_part)
        except ChannelError as exc:
            self.logger.error("QQ 目标非法 %s: %s", target.id, exc)
            return False, False
        channel = self.channels.get("qq")
        if channel is None:
            return self._qq_fallback(target, "QQ 渠道未装配", title, body)
        try:
            channel.send(OutboundMessage(chat_id=chat_id, text=body))
            self.logger.info("QQ 投递成功: %s", chat_id)
            return True, False
        except ChannelError as exc:
            return self._qq_fallback(target, f"QQ 渠道发送失败: {exc}",
                                     title, body)

    def _qq_fallback(self, target: SchedulerTarget, reason: str,
                     title: str, body: str) -> Tuple[bool, bool]:
        """渠道离线/发送失败的降级：回退桌面或跳过并记日志（可配置）。"""
        if target.fallback == "desktop" and self.desktop is not None:
            self.logger.warning(
                "QQ 投递失败（%s），回退桌面投递: %s", reason, target.id)
            return self._deliver_desktop(target, title, body), False
        self.logger.warning(
            "QQ 投递失败（%s），按 skip 策略跳过: %s", reason, target.id)
        return False, True   # skip 并记日志：策略性放弃


# ----------------------------------------------------------------------------
# 内部工具
# ----------------------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _parse_iso(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text))
    except ValueError:
        return None


def _build_trigger_text(spec: ScheduleSpec, due: datetime) -> str:
    """合成 ProactiveTrigger 文本（不是用户原话，恢复会话可区分）。

    harness.run_stream(source='schedule') 会把它以 kind=proactive 落盘；
    模型看到的是「系统发起的主动话头」而非用户输入。
    """
    return (
        "[主动触发] " + spec.title
        + "（计划时点 " + _iso(due) + "，本地时间）\n"
        + spec.proactive_message
    )
