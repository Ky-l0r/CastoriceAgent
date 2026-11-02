/**
 * 出站消息队列（连发合并 + 忙时排队）。
 *
 * 解决的问题（用户反馈原文）：
 * 1. 「不小心连续发很多条重复的消息时，AI 会反复回答」——根因是桥接层
 *    在 busy 时直接拒绝 chat 帧，但界面已经把消息画出来了；用户看到
 *    「消息发了却没回复」，就会再发一遍，于是每发一次都触发一轮回答。
 * 2. 「我发几条消息后，AI 在接下来的一段时间整理我都发了什么，再一一
 *    回复」——聊天模式下把连发的多条消息合并成一次请求，模型可以整体
 *    理解上下文后逐条回应，避免碎片化的一问一答。
 *
 * 交互约定（尽量不打扰）：
 * - 单条消息只延迟 1.2s 发出（给紧随其后的第二条留聚合机会），几乎无感；
 * - 未发送的消息达到阈值（设置项「连发合并条数」）后进入「整理窗口」：
 *   用户停止输入（设置项「停止输入多久算说完」，默认 8s）或窗口到期
 *   （设置项「整理窗口」，默认 180s）即合并发送；
 * - AI 正在回复时，新消息一律入队，等本轮 done 后按同样规则发出，
 *   不再静默丢弃，因此不会出现「同一句话问两遍」。
 *
 * 只作用于聊天模式；工作 / 编程 / 学习模式仍即时发送（那里每条消息
 * 往往对应一次工具执行，合并会破坏语义）。
 */
import { useBridgeStore } from "../store/bridge-store";

export interface OutgoingMessage {
  text: string;
  images?: string[];
}

/** 单条消息的聚合延迟：给「紧接着再发一条」留出合并机会。 */
const AGGREGATE_DELAY_MS = 1200;
/** busy 时等待 done 后重试的间隔兜底（订阅失效时也不会卡死）。 */
const BUSY_RETRY_MS = 800;

let queue: OutgoingMessage[] = [];
/** 本轮合并的第一条消息入队时刻（用于窗口上限）。 */
let firstAt = 0;
let timer: ReturnType<typeof setTimeout> | null = null;
let listening = false;
/**
 * 已经发出、但界面上还没看到 busy 的那一刻。
 *
 * 桥接的 chat 是单向帧（不等回执），而 busy 要等第一个 text 事件才会
 * 变 true。这中间有几十到几百毫秒的窗口——如果用户恰好在这时再按一次
 * 发送，再发一轮就会被后端以「已有对话在进行」拒绝，消息再次静默丢失
 * （这正是「反复回答」的上游）。用这个标志把窗口补上。
 */
let awaiting = false;
let awaitingTimer: ReturnType<typeof setTimeout> | null = null;
/** awaiting 的兜底解绑时间：模型迟迟不产事件时也不能把队列永久卡住。 */
const AWAIT_TIMEOUT_MS = 6000;

function syncPending(): void {
  const store = useBridgeStore.getState();
  if (store.pendingChat !== queue.length) store.setPendingChat(queue.length);
}

function clearTimer(): void {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
}

function schedule(delay: number): void {
  clearTimer();
  timer = setTimeout(() => {
    timer = null;
    flush();
  }, Math.max(0, delay));
}

/** 丢弃队列（例如切换会话：旧会话的待发消息不应发到新会话）。 */
export function resetChatQueue(): void {
  clearTimer();
  clearAwaiting();
  queue = [];
  firstAt = 0;
  syncPending();
}

function usesBatch(): boolean {
  const state = useBridgeStore.getState();
  return state.shouldBatchChat(state.mode);
}

/**
 * 把多条消息合成一次请求的正文。
 *
 * 不加任何「以下是用户连续发来的 N 条消息」之类的前缀：这段文本既会
 * 被模型读到，也会作为会话历史里的用户原文回放——注入语会泄露到界面。
 * 多行原文本身已经足够让模型理解「这是连着说的几句话」。
 */
function composeText(items: OutgoingMessage[]): string {
  if (items.length === 1) return items[0].text;
  return items.map((item) => item.text).join("\n");
}

function markAwaiting(): void {
  awaiting = true;
  if (awaitingTimer) clearTimeout(awaitingTimer);
  awaitingTimer = setTimeout(() => {
    awaitingTimer = null;
    awaiting = false;
    onIdle();
  }, AWAIT_TIMEOUT_MS);
}

function clearAwaiting(): void {
  awaiting = false;
  if (awaitingTimer) {
    clearTimeout(awaitingTimer);
    awaitingTimer = null;
  }
}

function flush(): void {
  clearTimer();
  if (queue.length === 0) {
    syncPending();
    return;
  }

  const store = useBridgeStore.getState();
  if (store.phase !== "ready" || store.busy || awaiting) {
    // 后端还没就绪 / 上一轮还没结束：等一轮再试（done 会触发 flush）。
    schedule(BUSY_RETRY_MS);
    return;
  }

  const items = queue;
  queue = [];
  firstAt = 0;
  syncPending();
  const text = composeText(items);
  const images = items.flatMap((item) => item.images ?? []);
  if (!text && images.length === 0) return;
  markAwaiting();
  void window.castorice.chat(text, images.length > 0 ? images : undefined);
}

function direct(item: OutgoingMessage): void {
  markAwaiting();
  void window.castorice.chat(item.text, item.images);
}

/** 连接就绪 / 本轮结束时调用：把攒下的消息发出去。 */
function onIdle(): void {
  if (queue.length === 0) return;
  flush();
}

function ensureListening(): void {
  if (listening) return;
  listening = true;
  // busy 从 true → false（一轮 done）时发队列；订阅只建一次。
  useBridgeStore.subscribe((state, prev) => {
    if (!prev.busy && state.busy) {
      // 本轮真的开始了：兜底计时器可以撤了（结束由下面的边沿负责）。
      if (awaitingTimer) {
        clearTimeout(awaitingTimer);
        awaitingTimer = null;
      }
      return;
    }
    if (prev.busy && !state.busy) {
      clearAwaiting();
      onIdle();
    }
    if (prev.phase !== "ready" && state.phase === "ready") onIdle();
  });
}

/**
 * 把一条用户消息放进发送流程。
 *
 * 调用方负责先把消息画进聊天流（pushUser），这里只负责「什么时候发、
 * 和别人合并成什么」。这样即使排队很久，用户也能立刻看到自己发出去的
 * 内容，不会以为没发成功而重复发送。
 */
export function enqueueChat(item: OutgoingMessage): void {
  ensureListening();
  const store = useBridgeStore.getState();

  // 非聊天模式 / 关闭了合并：即时发送，但 busy 时仍然排队（不丢弃）。
  if (!usesBatch()) {
    if (store.busy || store.phase !== "ready") {
      queue.push(item);
      syncPending();
      schedule(BUSY_RETRY_MS);
      return;
    }
    direct(item);
    return;
  }

  const threshold = Math.max(2, store.chatBatch.threshold);
  const windowMs = Math.max(5, store.chatBatch.windowSeconds) * 1000;
  const quietMs = Math.max(2, store.chatBatch.quietSeconds) * 1000;
  const now = Date.now();
  if (queue.length === 0) firstAt = now;
  queue.push(item);
  syncPending();

  // 还没攒够阈值：短暂延迟后直接发（除非期间又来了消息）。
  if (queue.length < threshold) {
    schedule(AGGREGATE_DELAY_MS);
    return;
  }

  // 达到阈值：进入整理窗口，最多等到 firstAt + windowMs；
  // 用户停止输入 quietMs 后提前发送。
  const due = Math.min(firstAt + windowMs, now + quietMs);
  schedule(due - now);
}
