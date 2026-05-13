/** chat store queue (BL-HERMES013-RED-1A ACP /queue 等价) 单测.
 *
 *   cd edge/companion-app
 *   npx tsx src/store/queue.test.ts
 *
 * 用 console.assert + 退码体现成败 (跟 triggers.test.ts / auto_continue.test.ts 同模式).
 */

// jsdom-free 假 localStorage (chat store init 不直接用, 但 import 链上有)
const _store = new Map<string, string>();
(globalThis as unknown as { localStorage: Storage }).localStorage = {
  length: 0,
  getItem: (k: string) => _store.get(k) ?? null,
  setItem: (k: string, v: string) => { _store.set(k, v); },
  removeItem: (k: string) => { _store.delete(k); },
  clear: () => _store.clear(),
  key: () => null,
};

import { useChatStore } from "./chat";

let pass = 0;
let fail = 0;

function check(name: string, ok: boolean, info?: string): void {
  if (ok) {
    pass++;
    console.log(`  ✓ ${name}`);
  } else {
    fail++;
    console.error(`  ✗ ${name}${info ? ` — ${info}` : ""}`);
  }
}

// ─── 初始 state ───────────────────────────────────────

console.log("[init]");

useChatStore.getState().clearQueue();
check("queue 默认空", useChatStore.getState().queue.length === 0);

// ─── enqueueMessage ────────────────────────────────────

console.log("[enqueue]");

useChatStore.getState().enqueueMessage("第一条排队");
check(
  "enqueue 1 条 → queue 长度 1",
  useChatStore.getState().queue.length === 1,
);
check(
  "queue[0].text 正确",
  useChatStore.getState().queue[0]?.text === "第一条排队",
);
check(
  "queue[0] 有 id (UUID 或 fallback)",
  typeof useChatStore.getState().queue[0]?.id === "string"
    && useChatStore.getState().queue[0]!.id.length > 0,
);
check(
  "queue[0] 有 ts ISO timestamp",
  /^\d{4}-\d{2}-\d{2}T/.test(useChatStore.getState().queue[0]?.ts || ""),
);

useChatStore.getState().enqueueMessage("第二条排队");
useChatStore.getState().enqueueMessage("第三条排队");
check(
  "enqueue 3 条 → queue 长度 3",
  useChatStore.getState().queue.length === 3,
);
check(
  "顺序正确 (FIFO 排队)",
  useChatStore.getState().queue[0]?.text === "第一条排队"
    && useChatStore.getState().queue[1]?.text === "第二条排队"
    && useChatStore.getState().queue[2]?.text === "第三条排队",
);

// ─── dequeueMessage (FIFO) ────────────────────────────

console.log("[dequeue]");

const head1 = useChatStore.getState().dequeueMessage();
check(
  "dequeue 第一次 → 返第一条 (FIFO)",
  head1?.text === "第一条排队",
);
check(
  "dequeue 后 queue 长度 2",
  useChatStore.getState().queue.length === 2,
);

const head2 = useChatStore.getState().dequeueMessage();
check(
  "dequeue 第二次 → 返第二条",
  head2?.text === "第二条排队",
);

const head3 = useChatStore.getState().dequeueMessage();
check(
  "dequeue 第三次 → 返第三条",
  head3?.text === "第三条排队",
);

const head4 = useChatStore.getState().dequeueMessage();
check(
  "dequeue 空 queue → 返 undefined",
  head4 === undefined,
);
check(
  "queue 空了",
  useChatStore.getState().queue.length === 0,
);

// ─── removeQueuedMessage (撤回特定一条) ───────────────

console.log("[removeQueuedMessage]");

useChatStore.getState().enqueueMessage("aa");
useChatStore.getState().enqueueMessage("bb");
useChatStore.getState().enqueueMessage("cc");
const middleId = useChatStore.getState().queue[1]?.id;
check(
  "queue 长度 3, 取中间 id",
  middleId !== undefined,
);

useChatStore.getState().removeQueuedMessage(middleId!);
check(
  "removeQueuedMessage(middleId) → 长度 2",
  useChatStore.getState().queue.length === 2,
);
check(
  "中间一条被移除, 顺序: aa cc",
  useChatStore.getState().queue[0]?.text === "aa"
    && useChatStore.getState().queue[1]?.text === "cc",
);

useChatStore.getState().removeQueuedMessage("non-existent-id");
check(
  "removeQueuedMessage(不存在 id) → 长度不变",
  useChatStore.getState().queue.length === 2,
);

// ─── clearQueue ────────────────────────────────────────

console.log("[clearQueue]");

useChatStore.getState().clearQueue();
check(
  "clearQueue 后 queue 空",
  useChatStore.getState().queue.length === 0,
);

// ─── reset() 也清 queue (跨 session 隔离) ──────────────

console.log("[reset clears queue]");

useChatStore.getState().enqueueMessage("跨会话不该留");
useChatStore.getState().enqueueMessage("第二条也不该留");
check(
  "enqueue 2 条 → 长度 2",
  useChatStore.getState().queue.length === 2,
);

useChatStore.getState().reset();
check(
  "reset() 后 queue 清零 (切会话不该带 queue)",
  useChatStore.getState().queue.length === 0,
);

// ─── 收尾 ────────────────────────────────────────────

console.log(`\n[result] ${pass} passed, ${fail} failed`);
if (fail > 0) {
  process.exit(1);
}
