import { EventEmitter } from "node:events";
import type { BridgeEvent, ClientFrame, ServerFrame } from "../shared/bridge-protocol";

export type ConnectionState = "connecting" | "ready" | "reconnecting" | "offline";

interface PendingRpc {
  resolve: (value: unknown) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

export interface BridgeClientOptions {
  url: () => string;
  token: () => string;
  /** 重连间隔起点与上限（指数退避）。 */
  retryBaseMs?: number;
  retryMaxMs?: number;
}

/**
 * 主进程 WS 客户端：自动重连 + token 握手 + 帧分发。
 *
 * 协议（castorice/bridge.py）：连接后首帧必须 {"type":"hello","token":...}，
 * 5s 内收 {"type":"hello_ok"} 才算就绪；随后 event/rpc_result/rpc_error 帧
 * 分发给订阅方。token 只存在于主进程——渲染进程通过 IPC 间接使用。
 */
export class BridgeClient extends EventEmitter {
  private ws: WebSocketLike | null = null;
  private state: ConnectionState = "offline";
  private pending = new Map<string, PendingRpc>();
  private rpcSeq = 0;
  private retryAttempt = 0;
  private closedByUs = false;
  private readonly retryBaseMs: number;
  private readonly retryMaxMs: number;

  constructor(
    private readonly options: BridgeClientOptions,
    private readonly WebSocketImpl: WebSocketCtor,
  ) {
    super();
    this.retryBaseMs = options.retryBaseMs ?? 1000;
    this.retryMaxMs = options.retryMaxMs ?? 10000;
  }

  get connectionState(): ConnectionState {
    return this.state;
  }

  connect(): void {
    this.closedByUs = false;
    this.open();
  }

  close(): void {
    this.closedByUs = true;
    for (const rpc of this.pending.values()) {
      clearTimeout(rpc.timer);
      rpc.reject(new Error("bridge client closed"));
    }
    this.pending.clear();
    this.ws?.close(1000, "client shutdown");
    this.ws = null;
    this.setState("offline");
  }

  /** JSON-RPC 调用；未连接或超时（10s）拒绝。 */
  invoke(method: string, params?: Record<string, unknown>): Promise<unknown> {
    if (!this.ws || this.ws.readyState !== 1) {
      return Promise.reject(new Error("bridge not connected"));
    }
    const id = "rpc-" + ++this.rpcSeq;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error("rpc timeout: " + method));
      }, 10000);
      this.pending.set(id, { resolve, reject, timer });
      this.send({ type: "rpc", id, method, params });
    });
  }

  chat(text: string, images?: string[]): void {
    this.send({ type: "chat", text, images });
  }

  interrupt(): void {
    this.send({ type: "interrupt" });
  }

  // ---- 内部 ----

  private open(): void {
    this.setState(this.retryAttempt === 0 ? "connecting" : "reconnecting");
    const ws = new this.WebSocketImpl(this.options.url());
    this.ws = ws;

    ws.on("open", () => {
      this.send({ type: "hello", token: this.options.token() });
    });

    ws.on("message", (data: unknown) => {
      let frame: ServerFrame;
      try {
        frame = JSON.parse(String(data)) as ServerFrame;
      } catch {
        return;
      }
      this.handleFrame(frame);
    });

    ws.on("close", (code: number) => {
      if (this.closedByUs) return;
      // 4001 = 鉴权失败。token 来自 sidecar 握手，出现 4001 说明
      // sidecar 被换过；按普通断线退避重连（sidecar 重启后新 token 生效）。
      this.emit("disconnected", code);
      this.failAllPending("connection closed");
      this.scheduleReconnect();
    });

    ws.on("error", () => {
      // close 事件随后必然触发，重连逻辑收敛在 close 分支。
    });
  }

  private handleFrame(frame: ServerFrame): void {
    switch (frame.type) {
      case "hello_ok":
        this.retryAttempt = 0;
        this.setState("ready");
        return;
      case "event":
        this.emit("event", frame.event as BridgeEvent);
        return;
      case "rpc_result": {
        const rpc = this.pending.get(frame.id);
        if (rpc) {
          this.pending.delete(frame.id);
          clearTimeout(rpc.timer);
          rpc.resolve(frame.result);
        }
        return;
      }
      case "rpc_error": {
        const rpc = this.pending.get(frame.id);
        if (rpc) {
          this.pending.delete(frame.id);
          clearTimeout(rpc.timer);
          rpc.reject(new Error("rpc error " + frame.error.code + ": " + frame.error.message));
        }
        return;
      }
    }
  }

  private send(frame: ClientFrame): void {
    if (this.ws && this.ws.readyState === 1) {
      this.ws.send(JSON.stringify(frame));
    }
  }

  private failAllPending(reason: string): void {
    for (const rpc of this.pending.values()) {
      clearTimeout(rpc.timer);
      rpc.reject(new Error(reason));
    }
    this.pending.clear();
  }

  private scheduleReconnect(): void {
    const delay = Math.min(this.retryBaseMs * 2 ** this.retryAttempt, this.retryMaxMs);
    this.retryAttempt++;
    setTimeout(() => {
      if (!this.closedByUs) this.open();
    }, delay);
  }

  private setState(state: ConnectionState): void {
    if (this.state !== state) {
      this.state = state;
      this.emit("state", state);
    }
  }
}

/** 最小 WS 接口（用 ws 包实例化，避免把 node 类型引入渲染侧共享文件）。 */
interface WebSocketLike {
  readyState: number;
  send(data: string): void;
  close(code?: number, reason?: string): void;
  on(event: "open", cb: () => void): void;
  on(event: "message", cb: (data: unknown) => void): void;
  on(event: "close", cb: (code: number) => void): void;
  on(event: "error", cb: (err: Error) => void): void;
}

type WebSocketCtor = new (url: string) => WebSocketLike;
