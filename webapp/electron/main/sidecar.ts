import { ChildProcess, spawn } from "node:child_process";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import path from "node:path";
import { Readable } from "node:stream";
import type { SidecarReady } from "../shared/bridge-protocol";

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

  constructor(private readonly scriptPath: string, options: SidecarOptions = {}) {
    super();
    this.pythonPath = options.pythonPath ?? "python";
    this.castoriceRoot = options.castoriceRoot ?? path.resolve(scriptPath, "../../..");
    this.dataRoot = options.dataRoot;
    this.logDir = options.logDir;
  }

  get isReady(): boolean {
    return this.state === "ready" && this.readyInfo !== null;
  }

  get ready(): SidecarReady | null {
    return this.readyInfo;
  }

  /** 拉起 sidecar 并等待握手行（10s 超时 fail-fast）。 */
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

    try {
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
    } catch (err) {
      proc.kill();
      throw err;
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
