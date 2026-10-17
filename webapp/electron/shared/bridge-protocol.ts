/**
 * 桥接协议类型（与 castorice/bridge.py 模块 docstring 一一对应）。
 * main / preload / renderer 三方共享的单一事实源。
 */

/**
 * 服务端 -> 客户端 event 帧的事件种类（恒 2 元组契约的 JSON 形态）。
 * notification 是附加事件（castorice/notify.WebNotifier 经 send_event 广播，
 * 不属于 chat 流四元组，不在 done 纪律内）。
 */
export type BridgeEventKind = "text" | "tool" | "emoji" | "done" | "notification";

export interface BridgeEvent {
  kind: BridgeEventKind;
  payload: string | Record<string, unknown>;
  /** 仅 done 事件携带：本轮是否正常完成。 */
  ok?: boolean;
}

/** 客户端 -> 服务端帧。 */
export type ClientFrame =
  | { type: "hello"; token: string }
  | { type: "chat"; text: string; images?: string[] }
  | { type: "interrupt" }
  | { type: "rpc"; id: string; method: string; params?: Record<string, unknown> };

/** 服务端 -> 客户端帧。 */
export type ServerFrame =
  | { type: "hello_ok" }
  | { type: "event"; event: BridgeEvent }
  | { type: "rpc_result"; id: string; result: unknown }
  | { type: "rpc_error"; id: string; error: { code: number; message: string } };

/** sidecar spawn-bridge.py stdout ready 行。 */
export interface SidecarReady {
  event: "ready";
  port: number;
  token: string;
}
