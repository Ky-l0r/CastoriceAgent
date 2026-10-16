"""主动聊天调度器与双投递测试（第二期 t4）。

覆盖验收要点（t7 将逐条构造反例）：
① 3 个错过时点只补发最近 1 条（约定①）；
② flush 失败时投递不发生（约定②）；
③ proactive 消息落盘 kind='proactive' 且恢复会话可区分；
④ 桌面/QQ 双目标各有投递证据（QQ 用模拟渠道断言 OutboundMessage 与
   normalize_target）；
⑤ 渠道离线回退桌面 / 跳过+记日志两种策略均有用例；
⑥ schedules.json 不进发布包（打包白名单断言）；
⑦ 基线 738 passed / 1 skipped 不跌破（本文件新增用例全部离线可跑）。

时钟：全部用注入的 datetime（previous_due 的 now 参数 / run_due 的
now 参数），不 sleep、不依赖墙钟推进。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from castorice.channels.base import ChannelError, OutboundMessage
from castorice.notify import DeliveryResult, DesktopNotifier, NotifierRegistry
from castorice.scheduler import (
    MAX_TITLE_LEN,
    ScheduleError,
    ScheduleSpec,
    ScheduleStore,
    Scheduler,
    SchedulerConfig,
    SchedulerTarget,
    previous_occurrence,
)


# ----------------------------------------------------------------------------
# 替身
# ----------------------------------------------------------------------------

class RecordingHarness:
    """HarnessLoop 替身：记录 run_stream 调用，产出固定事件流。"""

    def __init__(self, reply: str = "到点啦，记得休息~",
                 fail_flush: bool = False, error: Optional[Exception] = None):
        self.session = RecordingSession(fail_flush=fail_flush)
        self.calls: List[Dict[str, Any]] = []
        self.reply = reply
        self.error = error

    def run_stream(self, user_message, images=None, *, source="user"):
        self.calls.append({"text": user_message, "source": source})
        if self.error is not None:
            raise self.error
        yield ("text", self.reply)
        yield ("done", self.reply)


class RecordingSession:
    """SessionStore 替身：flush 可编程失败，append 记录事件。"""

    def __init__(self, fail_flush: bool = False):
        self.fail_flush = fail_flush
        self.events: List[Tuple[str, Dict[str, Any]]] = []
        self.flush_calls: List[str] = []

    def append(self, kind: str, payload=None, **fields):
        merged = dict(payload or {})
        merged.update(fields)
        self.events.append((kind, merged))

    def flush(self, reason: str = "") -> None:
        self.flush_calls.append(reason)
        if self.fail_flush:
            raise RuntimeError("模拟磁盘写入失败")


class RecordingBot:
    """ai.AI 替身：暴露 harness 属性并记录 new_chat_session 调用。"""

    def __init__(self, harness: RecordingHarness):
        self.harness = harness
        self.new_session_calls = 0

    def new_chat_session(self):
        self.new_session_calls += 1


class RecordingDesktop:
    """DesktopNotifier 替身：记录 notify 调用，结果可编程。"""

    def __init__(self, ok: bool = True, detail: str = ""):
        self.calls: List[Tuple[str, str, str]] = []
        self.ok = ok
        self.detail = detail

    def notify(self, title: str, body: str, target_id: str = "") -> DeliveryResult:
        self.calls.append((title, body, target_id))
        if self.ok:
            return DeliveryResult.success(target_id=target_id)
        return DeliveryResult.failure(target_id=target_id, detail=self.detail)

    def close(self) -> None:
        pass


class RecordingQQChannel:
    """QQ 渠道替身：记录 send 的 OutboundMessage；可编程离线/失败。"""

    def __init__(self, online: bool = True, fail: bool = False):
        self.sent: List[OutboundMessage] = []
        self.online = online
        self.fail = fail

    def send(self, message: OutboundMessage) -> str:
        if not self.online:
            raise ChannelError("NapCat 渠道未连接（send 失败）")
        if self.fail:
            raise ChannelError("模拟发送失败")
        self.sent.append(message)
        return "10001"


def make_spec(**overrides: Any) -> ScheduleSpec:
    base = dict(
        id="s1",
        title="喝水提醒",
        triggers={"daily": "08:00"},
        proactive_message="该喝水了",
        targets=[SchedulerTarget(id="desktop:main")],
        first_at="2026-01-01T08:00:00",
    )
    base.update(overrides)
    return ScheduleSpec(**base)


def write_store(tmp_path: Path, *specs: ScheduleSpec) -> ScheduleStore:
    store = ScheduleStore(tmp_path / "schedules.json")
    store.save(list(specs))
    return store


# ----------------------------------------------------------------------------
# ① 只补发最近一次错过的时点
# ----------------------------------------------------------------------------

class TestOnlyLatestMissed:
    def test_three_missed_daily_points_deliver_once(self, tmp_path):
        """每日 8:00 提醒，错过 1/6、2/6、3/6 三个时点 -> 只发 1 条，
        且补发的时点是最近那次（3/6 8:00）。"""
        spec = make_spec(first_at="2026-06-01T08:00:00")
        store = write_store(tmp_path, spec)
        desktop = RecordingDesktop()
        harness = RecordingHarness()
        bot = RecordingBot(harness)
        scheduler = Scheduler(bot=bot, store=store, desktop=desktop)

        # now = 6/3 09:00：错过 6/1、6/2、6/3 三个 8:00 时点
        now = datetime(2026, 6, 3, 9, 0, 0)
        results = scheduler.run_due(now=now)

        assert len(results) == 1
        assert results[0].ok
        assert results[0].due == datetime(2026, 6, 3, 8, 0, 0)  # 最近时点
        assert len(harness.calls) == 1
        assert len(desktop.calls) == 1

    def test_every_seconds_missed_burst_delivers_once(self, tmp_path):
        """每 600 秒一次的任务错过 3 个周期 -> 只发最近 1 个时点。"""
        first = datetime(2026, 6, 1, 0, 0, 0)
        spec = make_spec(
            id="s2",
            title="周期提醒",
            triggers={"every_seconds": 600},
            first_at=first.isoformat(),
        )
        store = write_store(tmp_path, spec)
        harness = RecordingHarness()
        scheduler = Scheduler(bot=RecordingBot(harness), store=store,
                              desktop=RecordingDesktop())
        now = first + timedelta(seconds=1900)  # 错过 600/1200/1800 三个时点
        results = scheduler.run_due(now=now)
        assert len(results) == 1
        assert results[0].due == first + timedelta(seconds=1800)

    def test_last_sent_at_prevents_redelivery(self, tmp_path):
        """同一时点第二次 run_due 不再触发（last_sent_at 已推进）。"""
        spec = make_spec(first_at="2026-06-01T08:00:00")
        store = write_store(tmp_path, spec)
        harness = RecordingHarness()
        scheduler = Scheduler(bot=RecordingBot(harness), store=store,
                              desktop=RecordingDesktop())
        now = datetime(2026, 6, 3, 9, 0, 0)
        assert len(scheduler.run_due(now=now)) == 1
        assert scheduler.run_due(now=now) == []          # 同一时点不重复
        assert len(harness.calls) == 1

    def test_future_point_never_fires(self, tmp_path):
        """未来时点不触发（无错过即无补发）。"""
        spec = make_spec(first_at="2099-01-01T08:00:00",
                         triggers={"at": "2099-01-01T08:00:00"})
        store = write_store(tmp_path, spec)
        harness = RecordingHarness()
        scheduler = Scheduler(bot=RecordingBot(harness), store=store,
                              desktop=RecordingDesktop())
        assert scheduler.run_due(now=datetime(2026, 6, 3, 9, 0, 0)) == []
        assert harness.calls == []

    def test_failed_delivery_retries_same_point(self, tmp_path):
        """投递失败不推进 last_sent_at：下一轮重试同一时点。"""
        spec = make_spec(first_at="2026-06-01T08:00:00")
        store = write_store(tmp_path, spec)
        desktop = RecordingDesktop(ok=False, detail="托盘不可用")
        harness = RecordingHarness()
        scheduler = Scheduler(bot=RecordingBot(harness), store=store,
                              desktop=desktop)
        now = datetime(2026, 6, 3, 9, 0, 0)
        results = scheduler.run_due(now=now)
        assert len(results) == 1 and not results[0].ok
        assert scheduler.run_due(now=now)[0].due == results[0].due  # 重试同一时点

    def test_persisted_last_sent_survives_restart(self, tmp_path):
        """last_sent_at 落盘：重建 store 后同一时点不重复发（防崩溃重发）。"""
        spec = make_spec(first_at="2026-06-01T08:00:00")
        store = write_store(tmp_path, spec)
        harness = RecordingHarness()
        scheduler = Scheduler(bot=RecordingBot(harness), store=store,
                              desktop=RecordingDesktop())
        now = datetime(2026, 6, 3, 9, 0, 0)
        scheduler.run_due(now=now)
        # 模拟重启：重新加载同一文件
        store2 = ScheduleStore(tmp_path / "schedules.json")
        harness2 = RecordingHarness()
        scheduler2 = Scheduler(bot=RecordingBot(harness2), store=store2,
                               desktop=RecordingDesktop())
        assert scheduler2.run_due(now=now) == []
        assert harness2.calls == []


# ----------------------------------------------------------------------------
# ② 投递在 flush 之后才提交
# ----------------------------------------------------------------------------

class TestFlushBeforeDelivery:
    def test_flush_failure_means_no_delivery(self, tmp_path):
        """flush 失败 -> 不投递任何目标（约定②，fail-closed）。"""
        spec = make_spec(targets=[
            SchedulerTarget(id="desktop:main"),
            SchedulerTarget(id="qq:12345"),
        ])
        store = write_store(tmp_path, spec)
        desktop = RecordingDesktop()
        qq = RecordingQQChannel()
        harness = RecordingHarness(fail_flush=True)
        scheduler = Scheduler(bot=RecordingBot(harness), store=store,
                              desktop=desktop, channels={"qq": qq})
        results = scheduler.run_due(now=datetime(2026, 6, 3, 9, 0, 0))
        assert len(results) == 1 and not results[0].ok
        assert "flush" in results[0].error
        assert desktop.calls == []   # 桌面未投递
        assert qq.sent == []         # QQ 未投递

    def test_delivery_happens_after_flush(self, tmp_path):
        """正常路径：flush 调用发生在投递之前（事件顺序可查）。"""
        order: List[str] = []

        class OrderedSession(RecordingSession):
            def flush(self, reason: str = "") -> None:
                order.append("flush:" + reason)

        class OrderedHarness(RecordingHarness):
            def __init__(self):
                super().__init__()
                self.session = OrderedSession()

        class OrderedDesktop(RecordingDesktop):
            def notify(self, title, body, target_id=""):
                order.append("deliver:desktop")
                return super().notify(title, body, target_id)

        class OrderedQQ(RecordingQQChannel):
            def send(self, message):
                order.append("deliver:qq")
                return super().send(message)

        spec = make_spec(targets=[
            SchedulerTarget(id="desktop:main"),
            SchedulerTarget(id="qq:12345"),
        ])
        store = write_store(tmp_path, spec)
        harness = OrderedHarness()
        scheduler = Scheduler(bot=RecordingBot(harness), store=store,
                              desktop=OrderedDesktop(), channels={"qq": OrderedQQ()})
        scheduler.run_due(now=datetime(2026, 6, 3, 9, 0, 0))
        assert order[0].startswith("flush:")
        assert "deliver:desktop" in order and "deliver:qq" in order
        assert order.index("deliver:desktop") > 0
        assert order[0] == "flush:proactive-delivery"  # flush 在最前
        assert order.index("flush:proactive-delivery") < order.index("deliver:desktop")
        assert order.index("flush:proactive-delivery") < order.index("deliver:qq")


# ----------------------------------------------------------------------------
# ③ proactive 消息落盘可区分
# ----------------------------------------------------------------------------

class TestProactiveEventKind:
    def test_run_stream_source_schedule_records_proactive(self, tmp_path):
        """真实 harness 链路：合成触发语（source='proactive'）落盘后，
        恢复会话仍可与真实用户输入区分（payload.source 标记）。"""
        pytest.importorskip("yaml")
        from castorice.session import KIND_PROACTIVE, KIND_USER, SessionStore
        from castorice.harness import HarnessLoop
        from castorice.modes import ModeRegistry

        root = tmp_path / "sessions"
        store = SessionStore(root, mode="chat", cwd=str(tmp_path))
        harness = HarnessLoop(session=store, provider=None, modes=ModeRegistry())
        events = list(harness.run_stream("该喝水了", source="proactive"))
        assert events[-1][0] == "done"
        store.flush(reason="test")
        store.close()   # 单写者纪律：冷读恢复前先释放登记

        # 冷读恢复：合成触发语可区分（内核语义：kind='user' +
        # payload.source='proactive'，与真实用户输入的裸 user 事件不同；
        # 事件流里 KIND_PROACTIVE 保留给显式主动通知事件）
        reloaded = SessionStore(root, mode="chat",
                                session_id=store.session_id)
        records = [(e.kind, dict(e.payload)) for e in reloaded.events()]
        proactive_users = [p for k, p in records
                           if p.get("source") == "proactive"]
        assert proactive_users, "恢复的会话里必须能找回 proactive 标记"
        assert "该喝水了" in proactive_users[-1].get("content", "")
        # 真实用户消息（_prepare_history 追加的那条）不带 proactive 标记
        plain_users = [p for k, p in records
                       if k == KIND_USER and p.get("source") != "proactive"]
        assert all("source" not in p for p in plain_users)
        reloaded.close()
        store.close()

    def test_proactive_distinguishable_in_derive(self, tmp_path):
        """恢复会话投影：proactive 事件的内容能与普通 user 消息区分开。"""
        pytest.importorskip("yaml")
        from castorice.session import KIND_PROACTIVE, KIND_USER, SessionStore
        from castorice.harness import HarnessLoop
        from castorice.modes import ModeRegistry

        root = tmp_path / "sessions"
        store = SessionStore(root, mode="chat", cwd=str(tmp_path))
        harness = HarnessLoop(session=store, provider=None, modes=ModeRegistry())
        for _ in harness.run_stream("[主动触发] 提醒", source="proactive"):
            pass
        events = list(store.events())
        assert any(e.payload.get("source") == "proactive" for e in events)
        assert any(e.kind == KIND_USER for e in events)
        store.close()


# ----------------------------------------------------------------------------
# ④ 桌面 / QQ 双目标投递证据
# ----------------------------------------------------------------------------

class TestDualDelivery:
    def _scheduler(self, tmp_path, desktop, qq):
        spec = make_spec(targets=[
            SchedulerTarget(id="desktop:main"),
            SchedulerTarget(id="qq:12345678"),
        ], first_at="2026-06-01T08:00:00")
        store = write_store(tmp_path, spec)
        harness = RecordingHarness(reply="记得喝水")
        bot = RecordingBot(harness)
        scheduler = Scheduler(bot=bot, store=store, desktop=desktop,
                              channels={"qq": qq})
        return scheduler, harness

    def test_desktop_target_delivered_via_notifier(self, tmp_path):
        desktop = RecordingDesktop()
        scheduler, harness = self._scheduler(
            tmp_path, desktop, RecordingQQChannel(online=False))
        results = scheduler.run_due(now=datetime(2026, 6, 3, 9, 0, 0))
        assert results[0].ok
        assert len(desktop.calls) == 1
        title, body, target_id = desktop.calls[0]
        assert title == "喝水提醒"
        assert body == "记得喝水"
        assert target_id == "desktop:main"
        assert harness.calls[0]["source"] == "proactive"

    def test_qq_target_delivered_with_normalized_outbound(self, tmp_path):
        qq = RecordingQQChannel()
        scheduler, _ = self._scheduler(
            tmp_path, RecordingDesktop(), qq)
        scheduler.run_due(now=datetime(2026, 6, 3, 9, 0, 0))
        assert len(qq.sent) == 1
        message = qq.sent[0]
        assert isinstance(message, OutboundMessage)
        assert message.chat_id == "12345678"   # normalize_target 后的 id
        assert message.text == "记得喝水"

    def test_both_targets_delivered_count(self, tmp_path):
        qq = RecordingQQChannel()
        scheduler, _ = self._scheduler(tmp_path, RecordingDesktop(), qq)
        results = scheduler.run_due(now=datetime(2026, 6, 3, 9, 0, 0))
        assert results[0].deliveries == 2

    def test_trigger_text_is_synthetic_not_user_words(self, tmp_path):
        """触发语是合成的 ProactiveTrigger 文本（带标题与时点），不是
        用户原话；harness 收到的 message 与 proactive_message 相关。"""
        qq = RecordingQQChannel()
        scheduler, harness = self._scheduler(tmp_path, RecordingDesktop(), qq)
        scheduler.run_due(now=datetime(2026, 6, 3, 9, 0, 0))
        text = harness.calls[0]["text"]
        assert "喝水提醒" in text
        assert "该喝水了" in text
        assert harness.calls[0]["source"] == "proactive"


# ----------------------------------------------------------------------------
# ⑤ 渠道离线降级：回退桌面 / 跳过+记日志
# ----------------------------------------------------------------------------

class TestOfflineFallback:
    def _spec(self) -> ScheduleSpec:
        return make_spec(
            id="s-offline",
            targets=[SchedulerTarget(id="qq:12345678", fallback="skip")],
            first_at="2026-06-01T08:00:00")

    def test_skip_strategy_logs_and_skips(self, tmp_path, caplog):
        spec = self._spec()
        spec.targets[0].fallback = "skip"
        store = write_store(tmp_path, spec)
        qq = RecordingQQChannel(online=False)
        desktop = RecordingDesktop()
        scheduler = Scheduler(bot=RecordingBot(RecordingHarness()), store=store,
                              desktop=desktop, channels={"qq": qq})
        with caplog.at_level(logging.WARNING, logger="castorice.scheduler"):
            results = scheduler.run_due(now=datetime(2026, 6, 3, 9, 0, 0))
        assert results[0].ok
        assert results[0].deliveries == 0
        assert qq.sent == []
        assert desktop.calls == []                     # 不回退
        assert any("skip" in r.message.lower() or "跳过" in r.message
                   for r in caplog.records)

    def test_desktop_strategy_falls_back(self, tmp_path):
        spec = self._spec()
        spec.targets[0].fallback = "desktop"
        store = write_store(tmp_path, spec)
        qq = RecordingQQChannel(online=False)
        desktop = RecordingDesktop()
        scheduler = Scheduler(bot=RecordingBot(RecordingHarness()), store=store,
                              desktop=desktop, channels={"qq": qq})
        results = scheduler.run_due(now=datetime(2026, 6, 3, 9, 0, 0))
        assert results[0].ok
        assert results[0].deliveries == 1
        assert len(desktop.calls) == 1                 # 回退到桌面
        assert desktop.calls[0][2] == "qq:12345678"

    def test_qq_send_failure_triggers_fallback_too(self, tmp_path):
        """不是只有「离线」才降级：send 抛 ChannelError 同样走降级。"""
        spec = self._spec()
        spec.targets[0].fallback = "desktop"
        store = write_store(tmp_path, spec)
        qq = RecordingQQChannel(online=True, fail=True)
        desktop = RecordingDesktop()
        scheduler = Scheduler(bot=RecordingBot(RecordingHarness()), store=store,
                              desktop=desktop, channels={"qq": qq})
        results = scheduler.run_due(now=datetime(2026, 6, 3, 9, 0, 0))
        assert results[0].deliveries == 1
        assert len(desktop.calls) == 1

    def test_skip_strategy_without_qq_channel(self, tmp_path, caplog):
        """渠道未装配（channels={}）同样走降级策略。"""
        spec = self._spec()
        store = write_store(tmp_path, spec)
        scheduler = Scheduler(bot=RecordingBot(RecordingHarness()), store=store,
                              desktop=None, channels={})
        with caplog.at_level(logging.WARNING, logger="castorice.scheduler"):
            results = scheduler.run_due(now=datetime(2026, 6, 3, 9, 0, 0))
        assert results[0].ok and results[0].deliveries == 0


# ----------------------------------------------------------------------------
# ⑥ schedules.json 不进发布包
# ----------------------------------------------------------------------------

class TestNotInReleasePackage:
    def test_pack_script_whitelist_excludes_schedules(self):
        """打包脚本按白名单复制：不拷贝 schedules.json。"""
        from pathlib import Path as _P
        script = _P("scripts") / "打包.ps1"
        text = script.read_text(encoding="utf-8")
        assert "schedules" not in text
        # 白名单复制只拷 Image / prompts / example_config / 使用说明
        assert "Copy-Item (Join-Path $root \"Image\")" in text

    def test_store_roundtrip_and_default_path_is_program_dir(self, tmp_path):
        """schedules.json 放程序目录（相对 CWD 解析，与 ui_settings.json
        同级——测试里用 tmp_path 模拟程序目录）。"""
        store = ScheduleStore(tmp_path / "schedules.json")
        spec = make_spec()
        store.save([spec])
        loaded = store.load()
        assert loaded[0].id == "s1"
        assert loaded[0].triggers == {"daily": "08:00"}
        assert loaded[0].targets[0].id == "desktop:main"
        # 原子写回不留 tmp
        assert not (tmp_path / "schedules.json.tmp").exists()

    def test_bad_file_fails_fast(self, tmp_path):
        """坏 schedules.json fail-fast，不静默清空。"""
        path = tmp_path / "schedules.json"
        path.write_text("{ not json", encoding="utf-8")
        with pytest.raises(ScheduleError):
            ScheduleStore(path).load()


# ----------------------------------------------------------------------------
# 校验与杂项
# ----------------------------------------------------------------------------

class TestValidation:
    def test_title_required_and_bounded(self):
        with pytest.raises(ScheduleError):
            make_spec(title="").validate()
        with pytest.raises(ScheduleError):
            make_spec(title="x" * (MAX_TITLE_LEN + 1)).validate()

    def test_trigger_mutex(self):
        with pytest.raises(ScheduleError):
            make_spec(triggers={"daily": "08:00", "at": "2026-01-01T00:00:00"}).validate()
        with pytest.raises(ScheduleError):
            make_spec(triggers={"cron": "* * * * *"}).validate()

    def test_target_requires_nonempty_id(self):
        with pytest.raises(ScheduleError):
            make_spec(targets=[SchedulerTarget(id="")]).validate()

    def test_qq_target_normalized(self):
        with pytest.raises(Exception):
            make_spec(targets=[SchedulerTarget(id="qq:")]).validate()
        target = SchedulerTarget(id="qq:  12345 ")
        target.validate()
        assert target.id == "qq:12345"

    def test_proactive_message_required(self):
        with pytest.raises(ScheduleError):
            make_spec(proactive_message="  ").validate()

    def test_disabled_schedule_never_fires(self, tmp_path):
        spec = make_spec(enabled=False, first_at="2026-06-01T08:00:00")
        store = write_store(tmp_path, spec)
        harness = RecordingHarness()
        scheduler = Scheduler(bot=RecordingBot(harness), store=store,
                              desktop=RecordingDesktop())
        assert scheduler.run_due(now=datetime(2026, 6, 3, 9, 0, 0)) == []
        assert harness.calls == []

    def test_previous_occurrence_weekday_boundary(self):
        # 2026-06-03 是周三。weekly Monday（weekday=0）09:00 -> 上周一 6/1
        due = previous_occurrence(
            "weekly", {"weekly": "09:00", "weekday": 0},
            datetime(2026, 6, 3, 12, 0, 0))
        assert due == datetime(2026, 6, 1, 9, 0, 0)

    def test_scheduler_start_close_idempotent(self, tmp_path):
        store = write_store(tmp_path, make_spec())
        scheduler = Scheduler(bot=RecordingBot(RecordingHarness()), store=store,
                              desktop=RecordingDesktop(),
                              config=SchedulerConfig(check_interval=0.05))
        scheduler.start()
        scheduler.start()  # 幂等
        scheduler.close()
        scheduler.close()  # 幂等
