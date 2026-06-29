/** BL-MULTI-SESSION-STREAM (5/24 鸿波 "切走任务还会继续吗") — 跨会话同时 stream 的注册中心.
 *
 * # 真问题
 *
 * 老架构: useChat 持有 AbortController + 单一 streamingId, ChatTab.handleSelect
 * 切会话时 cancel() abort 当前 stream → LLM 输出立刻断 + 工具调用链中断, 用户
 * 来回切会话像"打断小鲶不让把话说完", 长任务全废.
 *
 * # 新设计
 *
 * 模块级 Map<sessionId, StreamState>:
 *   - 每个 stream 自带 AbortController, 不依赖 React mount
 *   - 同步镜像一份 messages, 切走的会话流入这里, 切回来从这里恢复 UI
 *   - 完成 / 出错时清理自己
 *
 * # 跟 useChat / chat store 怎么协作
 *
 *   - useChat.send: 启动时 register stream, 把 sessionId + controller 落进来.
 *     send 内部仍然用 chat store 驱动 UI (因为 store 已经接到 React selector),
 *     但每次 onDelta / onToolCall 时也同步给 registry, 让"切走的会话"也能存住.
 *   - ChatTab.handleSelect 切会话: **不再 cancel**, 只是 loadSession + 让
 *     useChat 重新 subscribe registry. 老 stream 继续在 registry 里跑.
 *   - useChat 看 store.persistedSessionId 变化 → 如果 registry 有这个 session
 *     的 stream → 从 registry.messages 把 store messages 恢复出来.
 *   - ChatSidebar: 订阅 getInflightSessions(), 给进行中的会话画 ⏳.
 *
 * # 跟 db 持久化的关系
 *
 * 持久化逻辑 (sessionMessageAppend) 不动. 仍然 onDone 时一次性写完整 assistant
 * 消息. registry 只解决"流跑到一半时切走 UI 不丢" 的问题, 不替代 db 持久化.
 * stream 真挂了 (oom / crash) registry 跟着进程一起没, 跟老 inflight_streams
 * 行为对齐 (那个是 gateway 端).
 *
 * # 不变量
 *
 * - sessionId 由 ensureSessionId() 在 send 开头确定 (state.db 真 id), register
 *   传进来. 同一 sessionId 只能有一个 in-flight stream (二次 send 用同 id 会
 *   覆盖 — 行为同老 useChat 单 controller).
 * - 切到没 session id 的"新对话"前不能 register (caller 保证).
 * - clear() / abortAll() 只在 logout / dev hot reload 时调.
 */

import type { ChatMessage } from "../types/chat";

export interface StreamState {
  /** state.db 里的 session id (持久化用) */
  sessionId: string;
  /** 镜像的 messages — 跟 chat store 的 messages 字段同形状, 切走时从这恢复 */
  messages: ChatMessage[];
  /** 正在 stream 的 assistant message id (跟 chat store streamingId 对应) */
  streamingId: string | null;
  /** stream 是否还在跑 (false = done / cancelled / errored, 即将被 delete) */
  isStreaming: boolean;
  /** 本次 stream 用的 model 名 — 切会话恢复 UI 时知道 picker 该显啥 */
  model: string;
  /** 启动 unix ts ms, sidebar 显示"跑了多久" 时用 */
  startedAt: number;
  /** AbortController. cancel(sessionId) 通过它打断 */
  controller: AbortController;
}

const _streams = new Map<string, StreamState>();
/** 订阅者: sessionId → Set<callback>, "*" 是全局监听 (sidebar 用) */
const _listeners = new Map<string, Set<() => void>>();

const WILDCARD = "*";

function _emit(sessionId: string): void {
  _listeners.get(sessionId)?.forEach((cb) => {
    try {
      cb();
    } catch (e) {
      console.warn("[streamRegistry] listener threw:", e);
    }
  });
  // 任何 session 变更都通知全局监听 (sidebar)
  _listeners.get(WILDCARD)?.forEach((cb) => {
    try {
      cb();
    } catch (e) {
      console.warn("[streamRegistry] wildcard listener threw:", e);
    }
  });
}

/** 启动一个 stream, 返回新 controller. 同 sessionId 重启会先 abort 老的. */
export function start(
  sessionId: string,
  model: string,
  initialMessages: ChatMessage[],
): StreamState {
  // 防御: 同 sessionId 已经有 stream 在跑 → abort 老的再起新的
  const existing = _streams.get(sessionId);
  if (existing) {
    existing.controller.abort();
  }
  const state: StreamState = {
    sessionId,
    messages: [...initialMessages],  // 浅 copy, 后续在 registry 内自行 mutate
    streamingId: null,
    isStreaming: true,
    model,
    startedAt: Date.now(),
    controller: new AbortController(),
  };
  _streams.set(sessionId, state);
  _emit(sessionId);
  return state;
}

/** 拿 stream state (只读视角). 切回 streaming 中的会话用这个恢复 UI. */
export function get(sessionId: string): StreamState | undefined {
  return _streams.get(sessionId);
}

/** 改 state 的某些字段 — 调用方传 mutator, 内部完成后 emit 通知. */
export function update(
  sessionId: string,
  mutator: (s: StreamState) => void,
): void {
  const s = _streams.get(sessionId);
  if (!s) return;
  mutator(s);
  _emit(sessionId);
}

/** stream 自然结束 (done / error). 标 isStreaming=false, 留 messages 60 秒等切回恢复.
 *
 * P3.5.30 (6/17 鸿波): 5/24 老 finish() 立即 delete 是 BL-MULTI-SESSION-STREAM
 * 只 ship 一半遗症 — 用户 stream 中切走 tab, stream 完成后 finish() 删 registry,
 * 用户切回 Chat tab stale store 真等 ChatTab polling 5 秒 sync 才看 final.
 * 5/24 自留注释"未来加 keepAfterFinish 选项"这次补齐.
 *
 * 新行为:
 *   - 标 isStreaming = false, streamingId = null, emit 通知 (sidebar ⏳ 消失)
 *   - 保留 messages 在 map, ChatTab mount restore 时 get() 真能拿 final
 *   - 60 秒兜底 TTL auto-delete (防 ChatTab 永远不 mount 内存 leak)
 *   - ChatTab restore 后主动调 dismiss(sessionId) 立即清
 */
const POST_FINISH_KEEP_MS = 60_000;
const _finishTimers = new Map<string, ReturnType<typeof setTimeout>>();

export function finish(sessionId: string): void {
  const s = _streams.get(sessionId);
  if (!s) return;
  s.isStreaming = false;
  s.streamingId = null;
  _emit(sessionId);  // sidebar ⏳ 消失

  // 60 秒兜底: ChatTab 永远不 mount 真自动清**, 防 memory leak
  const old = _finishTimers.get(sessionId);
  if (old) clearTimeout(old);
  _finishTimers.set(
    sessionId,
    setTimeout(() => {
      _streams.delete(sessionId);
      _finishTimers.delete(sessionId);
      _emit(sessionId);
    }, POST_FINISH_KEEP_MS),
  );
}

/** ChatTab restore 后调, 立即清 stream state (替 60 秒 TTL).
 *
 * P3.5.30 (6/17 鸿波): ChatTab mount 消费 完 registry messages 后调.
 * 未消费 (用户没切回) 60 秒 TTL 自动清.
 */
export function dismiss(sessionId: string): void {
  const t = _finishTimers.get(sessionId);
  if (t) {
    clearTimeout(t);
    _finishTimers.delete(sessionId);
  }
  _streams.delete(sessionId);
  _emit(sessionId);
}

/** 主动 cancel, 老 stream 的 onError/onDone 会触发 finish(). */
export function cancel(sessionId: string): void {
  const s = _streams.get(sessionId);
  if (!s) return;
  s.controller.abort();
}

/** 当前是否有 in-flight stream. */
export function isInflight(sessionId: string): boolean {
  const s = _streams.get(sessionId);
  return !!s && s.isStreaming;
}

/** 所有 in-flight session id 列表 (sidebar 用). */
export function getInflightSessions(): string[] {
  return Array.from(_streams.keys()).filter((id) => _streams.get(id)?.isStreaming);
}

/** 订阅某 session 的变更. 传 "*" 订阅全部 (sidebar 用). 返 unsubscribe. */
export function subscribe(
  sessionId: string,
  cb: () => void,
): () => void {
  let set = _listeners.get(sessionId);
  if (!set) {
    set = new Set();
    _listeners.set(sessionId, set);
  }
  set.add(cb);
  return () => {
    _listeners.get(sessionId)?.delete(cb);
  };
}

/** 订阅 inflight 列表变化 (任何 stream start/finish 都触发). */
export function subscribeInflight(cb: () => void): () => void {
  return subscribe(WILDCARD, cb);
}

/** dev / logout 用. 生产代码别调. */
export function _abortAllForReset(): void {
  for (const s of _streams.values()) {
    s.controller.abort();
  }
  _streams.clear();
  _listeners.clear();
}
