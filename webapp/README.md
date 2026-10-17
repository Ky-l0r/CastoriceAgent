# CastoriceAgent Web UI（第三期：t2 脚手架 + t3 主界面）

Electron 壳 + React 渲染层 + Python sidecar 联动。t3 交付主界面：
左窄导航 + 欢迎页居中输入框 + 聊天流（打字机/tool 折叠/emoji 贴图）+
四模式 Tab + 审批卡（60s 倒计时）+ 深浅主题。设置中心 / 用户档案由
t4 在 src/pages 下展开。

## 技术栈（对齐 Cyrene-Agent 版本组合）

Electron 44 + Vite 8 + React 19 + Tailwind 4 + radix-ui + lucide-react + motion；
状态管理 zustand（WS 事件分发 store）+ jotai（备用原子状态）。

## 目录

    electron/
      main/index.ts        主进程组合根：窗口 / 托盘 / 单实例锁 / IPC 面
      main/sidecar.ts      Python sidecar 生命周期（spawn + stdout 握手）
      main/ws-client.ts    WS 客户端：自动重连 + token 握手 + 帧分发
      preload.ts           contextBridge 类型化 API（invoke / chat / 事件订阅）
      preload-api.d.ts     渲染进程 API 契约（三方共享类型的事实源）
      shared/bridge-protocol.ts  桥接协议类型（对齐 castorice/bridge.py）
      sidecar/spawn-bridge.py    Python 入口：AI + BridgeServer，stdout 握手行
    src/
      store/bridge-store.ts  zustand：连接状态 + transcript + mode/theme/approval
      lib/bridge-binding.ts  preload API <-> store 绑定 + 启动装配 + 审批轮询
      components/            TitleBar / SideNav / ModeTabs / ChatMessageList /
                             Composer / Avatar / ApprovalCard / NotificationBanner
      styles/global.css      主题 CSS 变量（对齐一期 Colors 深浅两套调色板）
      App.tsx                主界面组合根（欢迎页 ↔ 聊天流切换）

## 命令

    pnpm install
    pnpm dev              # 仅渲染层 Vite（浏览器调试，无桥接）
    pnpm dev:electron     # 构建渲染层后拉起 Electron（VITE_DEV=1，spawn sidecar）
    pnpm build            # 类型检查 + 渲染层 + main + preload 全量构建
    pnpm check:main       # tsc --noEmit（主进程）

## 链路（端到端）

    Electron main ──spawn──> python spawn-bridge.py
        │                        │ 构造 AI + BridgeServer
        │ <── stdout 首行 ────────│ {"event":"ready","port":N,"token":"..."}
        ├── BridgeClient(ws://127.0.0.1:N, hello token)
        │ <── hello_ok ──────────│
        │ <── event 帧 ──────────│ (text/tool/emoji/done)
    ipcRenderer.on("bridge:event") ──> zustand store ──> React

- token 只在主进程流转；渲染进程仅经 preload 类型化 API 间接使用。
- sidecar 崩溃：主进程 5s 后自动重启，WS 客户端指数退避重连（1s 起，10s 封顶）。
- dev 模式资源根 = 仓库根（CASTORICE_ROOT）；prod 从 process.resourcesPath 解析。

## 通知与托盘（t5）

- 主动聊天投递：spawn-bridge.py 组合根把 WebNotifier 注册为默认实现；
  调度器经 registry.notify() → 桥接 notification 事件 → 主进程
  notification.ts 分两路：窗口不可见时弹 Electron 系统通知（点击召回主窗），
  窗口内横幅（zustand banners）始终广播。
- 托盘：显示主窗 / 「关闭时最小化到托盘」勾选（持久化 userData/shell-prefs.json，
  渲染侧经 window:get/set-close-action IPC 读写）/ 退出；双击图标召回主窗。
