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


def _parent_alive_windows(ppid: int) -> bool:
    """Windows：用 OpenProcess + GetExitCodeProcess 判断父进程是否还活着。

    为什么不用 os.kill(ppid, 0)：Windows 上 Python 的 os.kill 对非子进程
    语义不可靠（0 号信号会走 TerminateProcess 以外的路径），容易误判。
    """
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(ppid))
        if not handle:
            # 打不开通常表示进程已退出（或权限不足——后者按存活处理，宁可不杀）
            return kernel32.GetLastError() == 5
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - 判断失败时按存活处理，避免误自杀
        return True


def _parent_alive(ppid: int) -> bool:
    """跨平台判断父进程是否存活（判断不了时一律按存活处理）。"""
    if ppid <= 0:
        return True
    if os.name == "nt":
        return _parent_alive_windows(ppid)
    try:
        os.kill(ppid, 0)
        return True
    except OSError:
        return False


def _install_parent_watchdog() -> None:
    """父进程（Electron）退出后**本进程必须自己退出**。

    为什么需要它：Windows 没有「父进程死亡自动清理子进程」的机制，
    Electron 被任务管理器强杀、或崩溃时 before-quit 不会执行，sidecar
    就会变成孤儿进程常驻后台（用户看到的就是「关了程序 python 还在跑」）。

    双保险：
      1. stdin EOF —— 父进程退出时管道自动关闭，stdin 读到 EOF 立即退出。
         依赖父进程以 stdio[0]='pipe' 启动本进程（Electron 侧已改）。
      2. PPID 轮询 —— 兜住 stdin 被意外继承/未关闭的情况（例如父进程
         的句柄被子进程继承）。每秒检查一次，代价可忽略。
    """
    parent_pid = int(os.environ.get("CASTORICE_PARENT_PID") or 0)
    logger = logging.getLogger("sidecar")

    def _watchdog() -> None:
        while True:
            time.sleep(1.0)
            if not _parent_alive(parent_pid):
                logger.warning("父进程 %s 已退出，sidecar 自行终止", parent_pid)
                # 用 os._exit 而不是 sys.exit：可能有非 daemon 线程
                # （chromadb / 渠道）在跑，正常退出流程可能被它们拖住。
                os._exit(0)

    def _stdin_is_pipe() -> bool:
        """stdin 是否是由父进程持有的管道。

        只有管道断开才意味着父进程走了。若 stdin 是 NUL / 文件 /
        控制台，PeekNamedPipe 会失败，此时不应触发退出——所以先用
        GetFileType 判断句柄类型，只对 PIPE 启用监控。

        注意：这里完全不碰 sys.stdin 对象。打包环境里访问它会初始化
        Python 的 IO 层，是启动死锁的诱因之一。
        """
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
            if not handle or handle == -1:
                return False
            FILE_TYPE_PIPE = 3
            return kernel32.GetFileType(handle) == FILE_TYPE_PIPE
        except Exception:  # noqa: BLE001 - 判断不了就当作不是管道
            return False

    def _watch_stdin() -> None:
        """监听 stdin 管道关闭（父进程退出）。

        **为什么不用 sys.stdin.readline()**：实测在 PyInstaller 打包版里，
        一个长期阻塞的 readline 线程会让后续 import（numpy / chromadb）
        永久卡死——进程 0% CPU、240s 不 ready。原因是 readline 在
        冻结环境里持有解释器级 IO 锁，与 PyInstaller 的 importer 互锁。
        这是本项目真实踩到的死锁（见 docs 里的启动故障复盘）。

        改用 PeekNamedPipe 轮询：不阻塞、不需要读线程持有任何锁，
        管道断开时返回 False。代价是每秒一次系统调用，可忽略。
        """
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        while True:
            time.sleep(1.0)
            try:
                avail = wintypes.DWORD(0)
                ok = kernel32.PeekNamedPipe(
                    handle, None, 0, None, ctypes.byref(avail), None)
                if not ok:
                    # 管道已断开：父进程走了
                    logger.info("stdin 管道已断开（父进程退出），sidecar 自行终止")
                    os._exit(0)
            except Exception:  # noqa: BLE001 - 查询失败不致死，下一轮再试
                continue

    if parent_pid > 0:
        threading.Thread(target=_watchdog, name="parent-watchdog",
                         daemon=True).start()
        logger.info("父进程监控已启用（ppid=%s）", parent_pid)

    # stdin 看门狗只在前者是管道时启用：它是「父进程已退出」的快速信号，
    # 不是必需的——不是管道时由上面的 PPID 轮询兜底。
    if _stdin_is_pipe():
        threading.Thread(target=_watch_stdin, name="stdin-watchdog",
                         daemon=True).start()
        logger.info("stdin 管道监控已启用")
    else:
        logger.info("stdin 不是管道，跳过 EOF 监控（依赖 PPID 轮询）")


def _install_stall_dumper(threshold_s: float = 90.0) -> None:
    """启动停滞时 dump 全部线程栈到 stderr。

    用途：用户报「启动很久没反应」时，stderr 里能直接看到卡在哪个调用
    （chromadb 初始化 / 网络 / 文件锁）。用 faulthandler 而不是外部
    profiler：进程内自省，不依赖用户安装额外工具。
    """
    import faulthandler

    def _arm() -> None:
        time.sleep(threshold_s)
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        sys.stderr.write("[sidecar] 启动超过 %.0fs，已 dump 线程栈\n" % threshold_s)
        sys.stderr.flush()

    threading.Thread(target=_arm, name="stall-dumper", daemon=True).start()


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

    Args:
        root: **数据目录**（渠道的 data_dir 与会话根都是可写数据；
            传程序目录会在覆盖更新时被抹掉）。

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
    # 父进程监控必须在任何重活之前装上：bot 构造（chromadb 加载）可能
    # 耗时数十秒，这期间父进程若被强杀，没有监控就会一直挂着。
    # 停滞诊断：设 CASTORICE_STALL_DUMP=秒数可缩短阈值（排查启动慢时用）
    try:
        _stall = float(os.environ.get("CASTORICE_STALL_DUMP") or 90.0)
    except ValueError:
        _stall = 90.0
    _install_stall_dumper(_stall)

    _install_parent_watchdog()

    # 程序目录：代码与只读资源（prompts / Image / 模板 config）。
    root = Path(
        os.environ.get("CASTORICE_ROOT") or str(REPO_ROOT)
    ).resolve()
    # prod（electron-builder extraResources）布局：资源根即 sidecar 脚本
    # 所在目录（resources/backend），castorice 包与 ai.py 都在这里；
    # 显式入 sys.path，不依赖「脚本目录恰好是仓库根」的 dev 推导。
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    # 数据目录：可写数据（config / 档案 / 记忆 / 会话）。
    # 与程序目录分离的理由见 castorice/paths.py 的模块 docstring：
    # 覆盖更新会整个替换程序目录，用户数据必须放在它之外。
    from castorice import migration as cmigration  # noqa: PLC0415

    # 先播种模板配置（新用户），再迁移老用户数据（不覆盖已存在文件）。
    data = cmigration.ensure_and_migrate(root)
    logging.getLogger("sidecar").info(
        "code_root=%s data_root=%s", root, data)

    _boot_logger = logging.getLogger("sidecar")
    try:
        _tb = time.time()
        from ai import AI
        _boot_logger.info("import ai 完成，耗时 %.2fs", time.time() - _tb)

        _t = time.time()
        bot = AI(
            config_path=str(data / "config.yaml"),
            prompts_dir=str(root / "prompts"),
            chroma_dir=str(data / "database"),
            image_dir=str(root / "Image"),
            sessions_dir=str(data / "sessions"),
        )
        # 构造耗时打日志：AI() 里含 chromadb 初始化与 embedding 模型
        # 加载，是启动阶段最大的一段。没有这条日志时，用户报「启动慢」
        # 只能靠猜是哪一步；有了它可以直接定位。
        _boot_logger.info("AI() 构造完成，耗时 %.2fs", time.time() - _t)
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
    # 注意传的是 data 而不是 root：渠道的可写数据不能落在程序目录里
    qq_channel = _start_napcat_channel(bot, data)
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
