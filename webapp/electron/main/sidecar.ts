import { ChildProcess, spawn } from "node:child_process";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import path from "node:path";
import { Readable } from "node:stream";
import type { SidecarReady } from "../shared/bridge-protocol";

export interface SidecarOptions {
  /** Python 解释器，默认 PATH 上的 python。 */
  pythonPath?: string;
  /** 资源根（CASTORICE_ROOT），dev 默认仓库根。 */
  castoriceRoot?: string;
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
  private readonly logDir?: string;

  constructor(private readonly scriptPath: string, options: SidecarOptions = {}) {
    super();
    this.pythonPath = options.pythonPath ?? "python";
    this.castoriceRoot = options.castoriceRoot ?? path.resolve(scriptPath, "../../..");
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
    const proc = this.scriptPath
      ? spawn(this.pythonPath, [this.scriptPath], {
          env: { ...process.env, CASTORICE_ROOT: this.castoriceRoot },
          stdio: ["ignore", "pipe", "pipe"],
          windowsHide: true,
        })
      : spawn(this.pythonPath, {
          env: { ...process.env, CASTORICE_ROOT: this.castoriceRoot },
          stdio: ["ignore", "pipe", "pipe"],
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

  /** 优雅终止：SIGTERM -> 3s 后强杀。 */
  stop(): void {
    const proc = this.proc;
    if (!proc) return;
    this.proc = null;
    const killed = setTimeout(() => {
      if (!proc.killed) proc.kill("SIGKILL");
    }, 3000);
    proc.once("exit", () => clearTimeout(killed));
    proc.kill();
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
