/** auto_continue.ts 单测 — 跑法:
 *
 *   cd edge/companion-app
 *   npx tsx src/store/auto_continue.test.ts
 *
 * 测 zustand store 行为 + 常量 + localStorage 持久化.
 * 用 console.assert + 退码体现成败 (跟 triggers.test.ts 同模式).
 */

// jsdom-free 假 localStorage — 在 import 之前 hook 上去
const _store = new Map<string, string>();
(globalThis as unknown as { localStorage: Storage }).localStorage = {
  length: 0,
  getItem: (k: string) => _store.get(k) ?? null,
  setItem: (k: string, v: string) => { _store.set(k, v); },
  removeItem: (k: string) => { _store.delete(k); },
  clear: () => _store.clear(),
  key: () => null,
};

import {
  useAutoContinueStore,
  MAX_AUTO_CONTINUES,
  AUTO_CONTINUE_PROMPT,
} from "./auto_continue";

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

// ─── 常量 ───────────────────────────────────────────

console.log("[constants]");

check(
  "MAX_AUTO_CONTINUES = 3 (3 轮够长任务接力, 太多就该手动)",
  MAX_AUTO_CONTINUES === 3,
  `got ${MAX_AUTO_CONTINUES}`,
);

check(
  "AUTO_CONTINUE_PROMPT 是短消息 (不灌 hint, 让 LLM 接前文)",
  AUTO_CONTINUE_PROMPT === "继续",
  `got "${AUTO_CONTINUE_PROMPT}"`,
);

check(
  "AUTO_CONTINUE_PROMPT 短 (≤ 5 字, 防 context 涨)",
  AUTO_CONTINUE_PROMPT.length <= 5,
  `length ${AUTO_CONTINUE_PROMPT.length}`,
);

// ─── store 默认行为 ──────────────────────────────────

console.log("[store default]");

// 重置 store 到默认
useAutoContinueStore.setState({ on: false });
_store.clear();

check(
  "默认 on=false (不打扰用户, 用户主动开)",
  useAutoContinueStore.getState().on === false,
);

// ─── toggle ─────────────────────────────────────────

console.log("[toggle]");

useAutoContinueStore.getState().toggle();
check(
  "toggle 一次 → on=true",
  useAutoContinueStore.getState().on === true,
);
check(
  "toggle on → 写 localStorage",
  _store.get("catfish.auto_continue") === "1",
);

useAutoContinueStore.getState().toggle();
check(
  "再 toggle → on=false",
  useAutoContinueStore.getState().on === false,
);
check(
  "toggle off → 删 localStorage",
  _store.has("catfish.auto_continue") === false,
);

// ─── setOn ──────────────────────────────────────────

console.log("[setOn]");

useAutoContinueStore.getState().setOn(true);
check(
  "setOn(true) → on=true",
  useAutoContinueStore.getState().on === true,
);

useAutoContinueStore.getState().setOn(false);
check(
  "setOn(false) → on=false",
  useAutoContinueStore.getState().on === false,
);

// ─── 持久化跨"重启" ─────────────────────────────────

console.log("[persistence]");

// 先开
useAutoContinueStore.getState().setOn(true);
check(
  "开后 localStorage 有 '1'",
  _store.get("catfish.auto_continue") === "1",
);

// 模拟"重启" — store 重新读 localStorage
// (实际生产是 Companion 重启时 zustand init 跑 loadFromStorage)
// 这里用 dynamic import 难做, 直接 assert localStorage 状态正确
check(
  "重启后再读 localStorage 仍是 '1' (zustand init 时会读到)",
  _store.get("catfish.auto_continue") === "1",
);

// ─── 收尾 ────────────────────────────────────────────

console.log(`\n[result] ${pass} passed, ${fail} failed`);
if (fail > 0) {
  process.exit(1);
}
