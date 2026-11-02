import { ChildProcess, spawn } from "node:child_process";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import path from "node:path";
import { Readable } from "node:stream";
import type { SidecarReady } from "../shared/bridge-protocol";

/**
 * 从 sidecar 的 stderr 尾巴里提炼一句可读的失败原因。
 *
 * Python 的 traceback 有好几行（`File "..."`、`raise ...`、缩进的调用栈），
 * 全塞进界面只会让人更懵。这里只留「真正描述问题的那一行」：优先取
 * 异常类型行（`ValueError: ...`），其次是最后一行非栈帧文本。
 */
function summarizeStderr(tail: string): string {
  const lines = tail
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("File \"") && !line.startsWith("Traceback"));
  if (lines.length === 0) return "";
  const exceptionLine = [...lines].reverse().find((line) => /^[A-Za-z_.]*(Error|Exception|Exit)\b/.test(line));
  const picked = exceptionLine ?? lines[lines.length - 1];
  return picked.length > 400 ? picked.slice(0, 400) + "…" : picked;
}

/**
 * 强杀一个进程及其所有子进程（Windows 用 taskkill /T，其它平台用进程组）。
 *
 * 存在理由：PyInstaller onedir 的 sidecar.exe 是引导器，真正干活的
 * Python 解释器是它的子进程。只 kill 外层会留下内层孤儿。
 */
function killTree(pid: number | undefined): void {
  if (!pid) return;
  if (process.platform === "win32") {
    try {
      // /T 连带子进程，/F 强制；输出丢弃，失败也不影响退出流程
      spawn("taskkill", ["/pid", String(pid), "/T", "/F"], {
        stdio: "ignore",
        windowsHide: true,
      });
    } catch {
      /* 进程可能已退出 */
    }
    return;
  }
  try {
    process.kill(-pid, "SIGKILL");
  } catch {
    try {
      process.kill(pid, "SIGKILL");
    } catch {
      /* 已退出 */
    }
  }
}

export interface SidecarOptions {
  /** Python 解释器，默认 PATH 上的 python。 */
  pythonPath?: string;
  /** 资源根（CASTORICE_ROOT），dev 默认仓库根。 */
  castoriceRoot?: string;
  /** 可写数据根（CASTORICE_DATA_ROOT）：config / 档案 / 记忆 / 会话。 */
  dataRoot?: string;
  /** stderr 日志落盘目录（dev 可省略）。 */
  logDir?: string;
  /** 握手超时毫秒数（默认 180s；见 start() 的注释）。 */
  handshakeTimeoutMs?: number;
}

export type SidecarState = "starting" | "ready" | "exited";

/**
 * Python sidecar 生命周期管理。
 *
 * 职责边界：只负责 spawn、stdout 握手行解析、崩溃退出事件。
 * WS 连接与重连由 BridgeClient 负责——sidecar 重启后客户端自然重连。
 * 握手契约见 electron/sidecar/spawn-bridge.py 模块 docstring：
 * stdout 首行 {"event":"ready","port":...,"token":...} 或
 * {"event":"error","message":...}；其余日志一律 stderr。
 */
export class Sidecar extends EventEmitter {
  private proc: ChildProcess | null = null;
  private state: SidecarState = "exited";
  private readyInfo: SidecarReady | null = null;
  private readonly pythonPath: string;
  private readonly castoriceRoot: string;
  private readonly dataRoot?: string;
  private readonly logDir?: string;
  /** 握手超时（毫秒）。打包版冷启动要加载 chromadb / embedding，留足余量。 */
  private readonly handshakeTimeoutMs: number;

  constructor(private readonly scriptPath: string, options: SidecarOptions = {}) {
    super();
    this.pythonPath = options.pythonPath ?? "python";
    this.castoriceRoot = options.castoriceRoot ?? path.resolve(scriptPath, "../../..");
    this.dataRoot = options.dataRoot;
    this.logDir = options.logDir;
    this.handshakeTimeoutMs = options.handshakeTimeoutMs ?? 180_000;
  }

  get isReady(): boolean {
    return this.state === "ready" && this.readyInfo !== null;
  }

  /** 是否还有子进程在跑（stop 后为 false）。 */
  get isRunning(): boolean {
    return this.proc !== null;
  }

  get ready(): SidecarReady | null {
    return this.readyInfo;
  }

  /**
   * 拉起 sidecar 并等待握手行。
   *
   * handshakeTimeoutMs 默认 180s：打包版首次启动要初始化 chromadb 与
   * embedding 模型（实测冷启动 15~60s，杀软扫描时更久）。超时不再无限
   * 等待，而是整棵进程树强杀后抛错，让上层按退避重试——否则界面会
   * 永远停在「正在启动后端…」。
   */
  async start(): Promise<SidecarReady> {
    if (this.proc) throw new Error("sidecar already running");
    this.state = "starting";
    this.readyInfo = null;

    // scriptPath 为空：pythonPath 本身是 PyInstaller 产物可执行文件
    //（绿色便携形态，不再依赖用户机器上的 Python）；否则按脚本 + 解释器拉起。
    // stdin 必须是 pipe（不能是 ignore）：
    //   1. 父进程退出时管道自动关闭，sidecar 读到 EOF 立即退出——
    //      这是 Windows 上唯一可靠的「父死子亡」信号；
    //   2. 同时把 PID 传过去，sidecar 另有 PPID 轮询兜底。
    // 传 process.pid 而不是 process.ppid：sidecar 的父进程就是本进程。
    const env: NodeJS.ProcessEnv = {
      ...process.env,
      CASTORICE_ROOT: this.castoriceRoot,
      CASTORICE_PARENT_PID: String(process.pid),
    };
    // 数据目录与程序目录分离（见 castorice/paths.py）：
    // 覆盖更新只替换程序目录，用户配置/记忆/会话留在数据目录里。
    if (this.dataRoot) {
      env.CASTORICE_DATA_ROOT = this.dataRoot;
      // 首次启动需要数据目录存在；sidecar 也会 ensure，不过提前建好
      // 能让崩溃日志、渠道数据目录的写入更早可用。
      try {
        fs.mkdirSync(this.dataRoot, { recursive: true });
      } catch {
        /* 建不出来交给 sidecar 的降级逻辑 */
      }
    }
    const proc = this.scriptPath
      ? spawn(this.pythonPath, [this.scriptPath], {
          env,
          stdio: ["pipe", "pipe", "pipe"],
          windowsHide: true,
        })
      : spawn(this.pythonPath, {
          env,
          stdio: ["pipe", "pipe", "pipe"],
          windowsHide: true,
        });
    this.proc = proc;

    proc.stderr?.setEncoding("utf8");
    proc.stderr?.on("data", (chunk: string) => {
      // 后端日志转发到主进程 stderr；prod 同时落盘。
      process.stderr.write("[sidecar] " + chunk);
      if (this.logDir) this.appendLog(chunk);
    });

    proc.on("exit", (code, signal) => {
      const wasReady = this.state === "ready";
      this.proc = null;
      this.state = "exited";
      this.readyInfo = null;
      this.emit("exit", { code, signal, wasReady });
    });

    // stdout 握手：逐行读，首行 JSON 决定成败。
    if (!proc.stdout) throw new Error("sidecar stdout unavailable");
    const lineStream = Readable.from(proc.stdout).setEncoding("utf8");

    // 握手的最后几行 stderr：sidecar 崩在启动阶段时，真正的原因只在这里
    // 出现（例如「配置文件格式错误: ... line 102」）。把它带进异常，
    // 界面才能显示用户能看懂、能自己修的原因。
    let stderrTail = "";
    proc.stderr?.on("data", (chunk: string) => {
      stderrTail = (stderrTail + chunk).slice(-1200);
    });

    const timeoutMs = this.handshakeTimeoutMs;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const timeout = new Promise<never>((_resolve, reject) => {
      timer = setTimeout(() => {
        reject(new Error(
          "后端启动超时（" + Math.round(timeoutMs / 1000) + " 秒）：" +
          "可能是首次启动正在加载记忆模型，或配置文件/端口存在异常"));
      }, timeoutMs);
    });

    const handshake = (async (): Promise<SidecarReady> => {
      for await (const line of lineStream as AsyncIterable<string>) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        let msg: Record<string, unknown>;
        try {
          msg = JSON.parse(trimmed);
        } catch {
          continue; // 非 JSON 行（理论上不该有）忽略
        }
        if (msg.event === "ready") {
          const info = msg as unknown as SidecarReady;
          if (!Number.isFinite(info.port) || typeof info.token !== "string") {
            throw new Error("sidecar ready frame malformed: " + trimmed);
          }
          this.readyInfo = info;
          this.state = "ready";
          return info;
        }
        if (msg.event === "error") {
          throw new Error("sidecar failed: " + String(msg.message));
        }
      }
      throw new Error("sidecar exited before handshake");
    })();

    try {
      return await Promise.race([handshake, timeout]);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      killTree(proc.pid);
      // 给 stderr 的最后一波数据一点时间落地（Python 的 traceback 往往在
      // 退出前一瞬才写出来），再把它提炼成一句人话。
      await new Promise((resolve) => setTimeout(resolve, 150));
      // 立刻放开内部状态：失败后上层会退避重试，而 if (this.proc)
      // 的「已在运行」检查会让重试白白失败几轮。
      if (this.proc === proc) this.proc = null;
      this.state = "exited";
      this.readyInfo = null;
      const reason = summarizeStderr(stderrTail);
      throw new Error(reason ? message + " · " + reason : message);
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  /**
   * 优雅终止：关 stdin（触发 sidecar 的 EOF 退出）-> SIGTERM -> 3s 后强杀进程树。
   *
   * 为什么要杀「进程树」而不是只 kill 自己：PyInstaller onedir 形态下
   * sidecar.exe 会再拉起一个真正的 Python 子进程，只杀外层 exe 会留下
   * 内层 python 常驻——这正是「关了程序后台还有 python」的另一个来源。
   */
  stop(): void {
    const proc = this.proc;
    if (!proc) return;
    this.proc = null;

    // 1. 关 stdin：sidecar 的 stdin-watchdog 读到 EOF 会自行退出
    //    （比 SIGTERM 更彻底：它会走 os._exit，不受非 daemon 线程拖延）。
    try {
      proc.stdin?.end();
    } catch {
      /* stdin 已关闭 */
    }

    let exited = false;
    proc.once("exit", () => {
      exited = true;
    });

    // 2. 温和信号
    try {
      proc.kill();
    } catch {
      /* 已退出 */
    }

    // 3. 兜底：3s 内没退出就整棵进程树强杀
    const timer = setTimeout(() => {
      if (exited) return;
      killTree(proc.pid);
    }, 3000);
    proc.once("exit", () => clearTimeout(timer));
  }

  private appendLog(chunk: string): void {
    if (!this.logDir) return;
    try {
      fs.mkdirSync(this.logDir, { recursive: true });
      fs.appendFileSync(path.join(this.logDir, "sidecar.log"), chunk);
    } catch {
      // 日志落盘失败不影响主流程
    }
  }
}
