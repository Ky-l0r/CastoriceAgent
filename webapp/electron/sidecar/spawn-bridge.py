"""Electron sidecar 启动脚本：构造 bot + BridgeServer，握手信息打到 stdout。

约定（Electron main 进程 sidecar.ts 依赖此契约）：
- stdout 第一行输出 {"event":"ready","port":<int>,"token":"..."} JSON；
- 启动失败输出 {"event":"error","message":"..."} 并以非零码退出；
- 所有其他日志一律走 stderr（webapp 侧日志文件也只收 stderr）；
- 进程收到 SIGTERM / Ctrl+C 时 server.close() 后退出。

组合根镜像 main.py:main(root)：AI 显式绝对路径构造（打包后不随 CWD
漂移），bot 构造失败立即 fail-fast——不出现「后端半启动但连不上」的
静默降级。harness.py 签名零改动：这里只消费 ai.AI 冻结接口。
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

# 脚本位于 <repo>/webapp/electron/sidecar/，仓库根在三级之上
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from castorice.bridge import BridgeServer  # noqa: E402

# Web 侧审批倒计时上限（对齐一期 ui.ChatWindow.APPROVAL_TIMEOUT_S 的 60s 语义；
# broker.timeout 可写时压进去，worker 侧阻塞等待超时即按 unavailable 拒绝）。
APPROVAL_TIMEOUT_S = 60.0

# request_id -> PermissionRequest 的待决登记（approval.detail RPC 的数据源；
# 线程安全：worker 线程在 responder 里写，RPC 线程池在 detail 里读）。
_pending_requests: dict = {}
_pending_lock = threading.Lock()


def _install_web_approval_responder(bot) -> bool:
    """把 Web 审批应答者接进 bot（返回是否成功接线）。

    语义与一期 ui.ChatWindow._on_permission_request 完全一致：
      1. broker.defer(request_id) 登记待决请求；
      2. 记录 PermissionRequest（approval.detail RPC 据此给前端渲染卡片）；
      3. return None —— worker 阻塞等待，前端 approval.resolve 唤醒，
         超时（broker.timeout）按 unavailable（拒绝）关闭，绝不静默放行。

    hasattr 探测对齐一期：MockBot / 旧实现没有审批接口时返回 False，
    桥接层照常工作，只是不会有工具弹出审批卡。
    """
    setter = getattr(bot, "set_approval_responder", None)
    if not callable(setter):
        return False
    broker = getattr(bot, "approval_broker", None)
    if broker is None:
        return False
    try:
        if hasattr(broker, "timeout"):
            broker.timeout = float(APPROVAL_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - 超时压不进去就用内核默认 120s
        pass

    def _on_permission_request(request, request_id: str = ""):
        try:
            broker.defer(request_id)
            with _pending_lock:
                _pending_requests[request_id] = request
        except Exception:  # noqa: BLE001
            return None
        return None

    try:
        setter(_on_permission_request)
    except Exception:  # noqa: BLE001
        return False
    return True


def _emit(payload: dict) -> None:
    """握手/错误信息走 stdout 单行 JSON，日志走 stderr。"""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _register_web_rpc_extensions(server, bot) -> None:
    """注册 Web 前端专用的审批辅助 RPC（在 BridgeServer 方法面之上追加）。

    * approval.detail {request_id} -> {detail: {...} | None}
      前端弹出审批卡前拉取请求详情（工具名 / 参数摘要 / 模式 / 原因）。
      桥接层自身的 approval.pending / approval.resolve 语义零改动。

    健壮性对齐 bridge.py：探测失败就跳过注册，不影响既有方法面。
    """
    def approval_detail(params: dict) -> dict:
        request_id = str(params.get("request_id") or "").strip()
        if not request_id:
            raise ValueError("approval.detail 需要 request_id")
        with _pending_lock:
            request = _pending_requests.get(request_id)
        if request is None:
            return {"detail": None}
        args = getattr(request, "arguments", None)
        if not isinstance(args, dict):
            args = {}
        try:
            arguments = {str(k): _preview_value(v) for k, v in args.items()}
        except Exception:  # noqa: BLE001 - 参数预览失败不挡卡片
            arguments = {}
        return {"detail": {
            "request_id": request_id,
            "tool": str(getattr(request, "tool", "") or ""),
            "mode": str(getattr(request, "mode", "") or ""),
            "reason": str(getattr(request, "reason", "") or ""),
            "target_paths": [str(p) for p in (getattr(request, "target_paths", ()) or ())],
            "arguments": arguments,
        }}

    routes = getattr(server, "_rpc_methods", None)
    if isinstance(routes, dict):
        # 桥接层方法路由就是一个 dict：直接登记扩展方法（方法面追加，不改原语义）。
        routes["approval.detail"] = approval_detail
    else:
        # 兜底：找不到可扩展点时记日志（审批卡退化为仅 pending id 列表）。
        logging.getLogger("sidecar").warning(
            "approval.detail RPC 未注册（桥接层无可扩展路由）")


def _start_napcat_channel(bot, root: Path):
    """组合根启动 QQ 渠道（config.yaml 的 channels.napcat 节，缺省不启用）。

    未配置该节时返回 None（保持第一期行为：完全不碰网络）。
    配置存在时构造 NapCatChannel 并 start()——首连 fail-fast 失败只记
    日志、不阻断后端启动（桌面 UI 仍需可用），下一次后端重启再重试。
    """
    logger = logging.getLogger("sidecar")
    try:
        from castorice.channels.base import ChannelContext
        from castorice.channels.napcat import NapCatChannel
    except Exception as exc:  # noqa: BLE001 - 依赖缺失不阻断启动
        logger.warning("QQ 渠道模块不可用: %s", exc)
        return None

    try:
        section = bot.config_manager.get("channels.napcat")
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 channels.napcat 失败: %s", exc)
        return None
    if not section:
        logger.info("未配置 channels.napcat，跳过 QQ 渠道")
        return None

    data_dir = root / "channels" / "qq"
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # noqa: BLE001 - 目录建不出来只降级告警
        logger.warning("QQ 渠道数据目录创建失败: %s", exc)

    ctx = ChannelContext(
        bot=bot,
        sessions_root=str(root / "sessions"),
        data_dir=str(data_dir),
        options=section,
    )
    channel = NapCatChannel(ctx)
    try:
        channel.start()
    except Exception as exc:  # noqa: BLE001 - fail-fast 但不阻断桌面
        logger.error("QQ 渠道连接失败（后端继续运行）: %s", exc)
        try:
            channel.close()
        except Exception:  # noqa: BLE001
            pass
        return None
    logger.info("QQ 渠道已启动: %s", ctx.options.get("url", ""))
    return channel


def _register_web_notifier(server) -> None:
    """组合根主动注册 WebNotifier（t5：主动聊天投递到 Electron 通知）。

    语义等价于前端调 notify.register RPC：WebNotifier 绑定桥接服务器后
    注册进进程级 NotifierRegistry 并设为默认——调度器的主动聊天经
    registry.notify() 到 WebNotifier.send_event("notification") 广播给
    Electron 壳（系统通知 + 窗口横幅双路）。
    失败不阻断启动（通知是锦上添花，软失败纪律）。
    """
    try:
        from castorice.notify import WebNotifier, get_notifier_registry

        notifier = WebNotifier()
        notifier.bind(server)
        get_notifier_registry().register("web", notifier, default=True)
        logging.getLogger("sidecar").info("web notifier registered as default")
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("sidecar").warning("WebNotifier 注册失败（通知降级）: %s", exc)


def _preview_value(value, limit: int = 200) -> str:
    """参数值预览：JSON 序列化截断，仅用于卡片展示（字符串不再包引号）。"""
    if isinstance(value, str):
        text = value
    else:
        try:
            import json as _json
            text = _json.dumps(value, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def main() -> int:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # 与 main.py:setup_environment 同一资源定位：打包后资源根由
    # CASTORICE_ROOT 环境变量显式传入，dev 默认仓库根。
    root = Path(
        os.environ.get("CASTORICE_ROOT") or str(REPO_ROOT)
    ).resolve()
    # prod（electron-builder extraResources）布局：资源根即 sidecar 脚本
    # 所在目录（resources/backend），castorice 包与 ai.py 都在这里；
    # 显式入 sys.path，不依赖「脚本目录恰好是仓库根」的 dev 推导。
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        from ai import AI

        bot = AI(
            config_path=str(root / "config.yaml"),
            prompts_dir=str(root / "prompts"),
            chroma_dir=str(root / "database"),
            image_dir=str(root / "Image"),
            sessions_dir=str(root / "sessions"),
        )
    except Exception as exc:  # noqa: BLE001 - fail-fast 通知壳层
        logging.getLogger("sidecar").error("bot 构造失败: %s", exc, exc_info=True)
        _emit({"event": "error", "message": f"初始化失败：{exc}"})
        return 1

    approval_ok = _install_web_approval_responder(bot)
    logging.getLogger("sidecar").info(
        "web approval responder: %s", "installed" if approval_ok else "unavailable")

    server = BridgeServer(bot)
    _register_web_rpc_extensions(server, bot)
    _register_web_notifier(server)
    qq_channel = _start_napcat_channel(bot, root)
    try:
        port = server.start()
    except Exception as exc:  # noqa: BLE001
        _emit({"event": "error", "message": f"桥接启动失败：{exc}"})
        if qq_channel is not None:
            try:
                qq_channel.close()
            except Exception:  # noqa: BLE001
                pass
        return 1

    _emit({"event": "ready", "port": port, "token": server.token})
    logging.getLogger("sidecar").info("bridge ready on 127.0.0.1:%d", port)

    stop = {"flag": False}

    def _shutdown(signum, frame):  # noqa: ANN001, ARG001
        stop["flag"] = True

    signal.signal(signal.SIGINT, _shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _shutdown)

    try:
        while not stop["flag"]:
            signal.pause() if hasattr(signal, "pause") else __import__("time").sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        if qq_channel is not None:
            try:
                qq_channel.close()
            except Exception as exc:  # noqa: BLE001 - 关闭阶段不再抛
                logging.getLogger("sidecar").warning("QQ 渠道关闭异常: %s", exc)
        server.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
