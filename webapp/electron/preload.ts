import { contextBridge, ipcRenderer } from "electron";
import type { BridgeEvent } from "./shared/bridge-protocol";

/**
 * preload：向渲染进程暴露类型化 API。
 *
 * 边界纪律：这里不做任何业务逻辑——invoke/chat/interrupt 是主进程
 * IPC 的直通包装；事件订阅是主进程广播的转发。渲染进程永远不接触
 * token / 端口 / sidecar 进程句柄（token 只在主进程 WS 客户端里使用）。
 */

const api = {
  invoke: (method: string, params?: Record<string, unknown>) =>
    ipcRenderer.invoke("bridge:invoke", method, params),

  chat: (text: string, images?: string[]) =>
    ipcRenderer.invoke("bridge:chat", text, images),

  interrupt: () => ipcRenderer.send("bridge:interrupt"),

  onEvent: (listener: (event: BridgeEvent) => void) => {
    const wrapped = (_: unknown, event: BridgeEvent) => listener(event);
    ipcRenderer.on("bridge:event", wrapped);
    return () => ipcRenderer.removeListener("bridge:event", wrapped);
  },

  onConnectionState: (
    listener: (state: "connecting" | "ready" | "reconnecting" | "offline") => void,
  ) => {
    const wrapped = (_: unknown, state: string) =>
      listener(state as "connecting" | "ready" | "reconnecting" | "offline");
    ipcRenderer.on("bridge:connection-state", wrapped);
    return () => ipcRenderer.removeListener("bridge:connection-state", wrapped);
  },

  // ---- 窗口控制（自绘标题栏；主进程直通）----
  minimize: () => ipcRenderer.send("window:minimize"),
  hide: () => ipcRenderer.send("window:hide"),

  // ---- 通知横幅（t5：WebNotifier 主动聊天投递的窗口内一路）----
  onNotification: (
    listener: (payload: { title: string; body: string; target_id: string }) => void,
  ) => {
    const wrapped = (_: unknown, payload: { title: string; body: string; target_id: string }) =>
      listener(payload);
    ipcRenderer.on("bridge:notification", wrapped);
    return () => ipcRenderer.removeListener("bridge:notification", wrapped);
  },

  // ---- 关闭行为（t5 可配置：关闭时最小化到托盘）----
  getCloseAction: () => ipcRenderer.invoke("window:get-close-action"),
  setCloseAction: (value: boolean) => ipcRenderer.invoke("window:set-close-action", value),
};

export type CastoriceBridgeApi = typeof api;

contextBridge.exposeInMainWorld("castorice", api);
