/** BL-HERMES013-RED-1B (5/13 鸿波 ACP /steer 等价 — 路径 A 断流 + 续接) 单测.
 *
 *   cd edge/companion-app
 *   npx tsx src/lib/steer.test.ts
 *
 * 覆盖:
 *  1. applySteerPrefix — 没 _steered 透传 / 有 partial / partial 超 200 字截尾 /
 *                       partial 全空白 / 中文 partial / 多行 partial
 *  2. ChatMessage type 允许 _steered 字段 (compile time + runtime construct)
 *  3. store 携带 _steered 字段 roundtrip (不被吃掉)
 *
 * 跟 queue.test.ts / auto_continue.test.ts 同模式 — console.assert + 退码.
 */

// jsdom-free 假 localStorage (chat store init 链上有 zustand persist)
const _store = new Map<string, string>();
(globalThis as unknown as { localStorage: Storage }).localStorage = {
  length: 0,
  getItem: (k: string) => _store.get(k) ?? null,
  setItem: (k: string, v: string) => { _store.set(k, v); },
  removeItem: (k: string) => { _store.delete(k); },
  clear: () => _store.clear(),
  key: () => null,
};

import { applySteerPrefix } from "./steer";
import { useChatStore } from "../store/chat";
import type { ChatMessage } from "../types/chat";

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

// ─── applySteerPrefix ─────────────────────────────────

console.log("[applySteerPrefix]");

const passthrough = applySteerPrefix("用户原话", undefined);
check(
  "无 _steered → 原文透传, 不动",
  passthrough === "用户原话",
  `got: ${passthrough}`,
);

const empty = applySteerPrefix("", undefined);
check("无 _steered + 空 content → 空字符串", empty === "");

const withPartial = applySteerPrefix("我们换条思路", { atContent: "我先 read_file ABC..." });
check(
  "有 partial → 含 [STEER] 前缀",
  withPartial.startsWith("[STEER · 用户中途插话]"),
  `got: ${withPartial}`,
);
check(
  "有 partial → 含'打断你时你正说到'",
  withPartial.includes("打断你时你正说到"),
);
check(
  "有 partial → 含 partial 内容",
  withPartial.includes("我先 read_file ABC..."),
);
check(
  "有 partial → 含用户原话",
  withPartial.includes("我们换条思路"),
);

// 超 200 字 partial → 砍尾
const longPartial = "a".repeat(500);
const longResult = applySteerPrefix("改方向", { atContent: longPartial });
check(
  "超 200 字 partial → 取末尾 200 字 (不含完整 500)",
  !longResult.includes("a".repeat(201)),
  `不该 >= 201 个 a 连续`,
);
check(
  "超 200 字 partial → 末尾 200 个 a 在",
  longResult.includes("a".repeat(200)),
);

// 空 / 全空白 partial → 不破坏, 用兜底文案
const emptyPartial = applySteerPrefix("改方向", { atContent: "" });
check(
  "空 partial → 用兜底 '我打断了你, 你还没开口'",
  emptyPartial.includes("打断了你, 你还没开口"),
  `got: ${emptyPartial}`,
);

const blankPartial = applySteerPrefix("改方向", { atContent: "   \n\n  " });
check(
  "全空白 partial → 也走兜底",
  blankPartial.includes("打断了你, 你还没开口"),
);

// 中文 partial 不破坏
const cnPartial = applySteerPrefix("不对啊", {
  atContent: "我打算先调用 read_file 读 Excel 然后用 pandas 处理...",
});
check(
  "中文 partial 在 prefix 里",
  cnPartial.includes("我打算先调用 read_file 读 Excel"),
);

// 多行 partial: 不该破坏拼装
const multiLinePartial = applySteerPrefix("改一下", {
  atContent: "Step 1: 读文件\nStep 2: 解析\nStep 3: 写报告",
});
check(
  "多行 partial 拼装不挂",
  multiLinePartial.includes("Step 3: 写报告") || multiLinePartial.includes("Step 2: 解析"),
);

// content 含特殊字符 — 别被破坏
const special = applySteerPrefix("改方向 [STEER] 假提示", {
  atContent: "test",
});
check(
  "content 自己含 [STEER] 不被混淆 (用户原意保留)",
  special.includes("改方向 [STEER] 假提示"),
);

// ─── ChatMessage type roundtrip ──────────────────────

console.log("[ChatMessage type _steered]");

const steeredMsg: ChatMessage = {
  id: "test-1",
  role: "user",
  content: "改方向",
  ts: new Date().toISOString(),
  status: "done",
  _steered: { atContent: "LLM partial 内容" },
};
check(
  "ChatMessage 接受 _steered 字段 (compile time)",
  steeredMsg._steered?.atContent === "LLM partial 内容",
);

// 没 _steered 字段时, 类型上 optional, 不报错
const normalMsg: ChatMessage = {
  id: "test-2",
  role: "user",
  content: "普通消息",
  ts: new Date().toISOString(),
  status: "done",
};
check(
  "ChatMessage 不带 _steered 时 — undefined",
  normalMsg._steered === undefined,
);

// ─── store roundtrip ──────────────────────────────────

console.log("[store roundtrip _steered]");

useChatStore.getState().reset();

useChatStore.getState().addMessage(steeredMsg);
const stored = useChatStore.getState().messages[0];
check(
  "store addMessage 保留 _steered 字段",
  stored._steered?.atContent === "LLM partial 内容",
);

useChatStore.getState().addMessage(normalMsg);
const normalStored = useChatStore.getState().messages[1];
check(
  "store 普通 msg 没有 _steered (undefined)",
  normalStored._steered === undefined,
);

// updateMessage 不该污染 _steered
useChatStore.getState().updateMessage("test-1", { content: "改方向 v2" });
const updated = useChatStore.getState().messages.find((m) => m.id === "test-1");
check(
  "updateMessage 不丢 _steered",
  updated?._steered?.atContent === "LLM partial 内容",
  `got: ${JSON.stringify(updated?._steered)}`,
);

// reset() 清掉 messages (含 _steered)
useChatStore.getState().reset();
check(
  "reset 清空 messages — _steered msg 也被清",
  useChatStore.getState().messages.length === 0,
);

// ─── 报告 ─────────────────────────────────────────────

console.log(`\n[steer 等价 单测] pass=${pass} fail=${fail}`);
if (fail > 0) {
  process.exit(1);
}
