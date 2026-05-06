/** triggers.ts 手动单测 — 跑法:
 *
 *   cd edge/companion-app
 *   npx tsx src/lib/triggers.test.ts
 *
 * 没用 vitest (项目未装), 用 console.assert + 退码体现成败.
 * 信号检测都是纯函数, 这种轻量测够用. CI 接的话再换 vitest.
 */

import {
  detectSilence,
  detectDeadline,
  detectFocusReturn,
  shouldStaySilent,
  detectAnyTrigger,
} from "./triggers";

import type { ChatMessage } from "../types/chat";

let pass = 0;
let fail = 0;

function check(name: string, ok: boolean, info?: string) {
  if (ok) {
    pass++;
    console.log(`  ✓ ${name}`);
  } else {
    fail++;
    console.log(`  ✗ ${name}${info ? " — " + info : ""}`);
  }
}

const NOW = new Date(2026, 4, 6, 14, 0); // 5/6 14:00 工作时段

function freshMsg(role: "user" | "assistant", text: string, ts: Date): ChatMessage {
  return {
    id: Math.random().toString(36),
    role,
    content: text,
    ts: ts.toISOString(),
    status: "done",
  };
}

console.log("\n## detectSilence");
{
  const messages = [freshMsg("user", "我去开会一下, 等会回来弄上会材料", new Date(NOW.getTime() - 35 * 60_000))];
  const r = detectSilence({ messages, now: NOW });
  check("末尾 user 35min 前 + 含动词 → 命中", r != null && r.kind === "silence");

  const messages2 = [freshMsg("user", "我去开会, 弄材料", new Date(NOW.getTime() - 10 * 60_000))];
  check("沉默 < 30min → 不命中", detectSilence({ messages: messages2, now: NOW }) == null);

  const messages3 = [freshMsg("user", "好", new Date(NOW.getTime() - 60 * 60_000))];
  check("末尾不含动词 → 不命中", detectSilence({ messages: messages3, now: NOW }) == null);

  check("空 messages → 不命中", detectSilence({ messages: [], now: NOW }) == null);
}

console.log("\n## detectDeadline");
{
  // 用 NOW=5/6, 内含 5/8 (3 天内)
  const journal = "5/4 提到上会材料 5/8 前要交";
  const r = detectDeadline({ journalText: journal, now: NOW });
  check("journal 提 5/8 (2 天后) → 命中", r != null && r.kind === "deadline", JSON.stringify(r));

  const journal2 = "4 月份的事 4/1 deadline";
  check("已过的日期 → 不命中", detectDeadline({ journalText: journal2, now: NOW }) == null);

  check("空 journal → 不命中", detectDeadline({ journalText: "", now: NOW }) == null);
}

console.log("\n## detectFocusReturn");
{
  const messages = [freshMsg("user", "我去开会", new Date(NOW.getTime() - 50 * 60_000))];
  const r = detectFocusReturn({
    lastFocusLeftTs: NOW.getTime() - 40 * 60_000,
    lastFocusReturnTs: NOW.getTime() - 30_000, // 30s 前回来
    now: NOW,
    messages,
  });
  check("离开 40min 刚回来 → 命中", r != null && r.kind === "focus", JSON.stringify(r));

  const r2 = detectFocusReturn({
    lastFocusLeftTs: NOW.getTime() - 10 * 60_000,
    lastFocusReturnTs: NOW.getTime() - 30_000,
    now: NOW,
    messages: [freshMsg("user", "test", new Date(NOW.getTime() - 30 * 60_000))],
  });
  check("离开 < 30min → 不命中", r2 == null);

  const r3 = detectFocusReturn({
    lastFocusLeftTs: NOW.getTime() - 50 * 60_000,
    lastFocusReturnTs: NOW.getTime() - 5 * 60_000, // 5min 前回来, 早过 90s
    now: NOW,
    messages: [freshMsg("user", "test", new Date(NOW.getTime() - 60 * 60_000))],
  });
  check("回来过 90s → 不命中", r3 == null);
}

console.log("\n## shouldStaySilent");
{
  const r = shouldStaySilent({
    now: new Date(2026, 4, 6, 23, 0),
    firedLog: [],
    lastDismissTs: null,
    candidateKind: "silence",
  });
  check("夜间 23:00 → 静默", r.silent && r.reason.includes("quiet_hours"));

  const todayMs = NOW.getTime();
  const fired = Array.from({ length: 5 }, (_, i) => ({
    kind: "silence" as const,
    ts: todayMs - i * 60_000,
  }));
  const r2 = shouldStaySilent({ now: NOW, firedLog: fired, lastDismissTs: null, candidateKind: "deadline" });
  check("今天 fired 5 次 → 静默", r2.silent && r2.reason.includes("daily_cap"));

  const r3 = shouldStaySilent({
    now: NOW,
    firedLog: [],
    lastDismissTs: NOW.getTime() - 10 * 60_000,
    candidateKind: "silence",
  });
  check("最近 dismiss 过 → 静默", r3.silent && r3.reason === "recent_dismiss");

  const r4 = shouldStaySilent({
    now: NOW,
    firedLog: [{ kind: "silence", ts: NOW.getTime() - 10 * 60_000 }],
    lastDismissTs: null,
    candidateKind: "silence",
  });
  check("同 kind 30min 内 → 静默", r4.silent && r4.reason.includes("same_kind_cooldown"));

  const r5 = shouldStaySilent({
    now: NOW,
    firedLog: [],
    lastDismissTs: null,
    candidateKind: "silence",
  });
  check("工作时段 + 0 fired + 无 dismiss → 不静默", !r5.silent);
}

console.log("\n## detectAnyTrigger 优先级");
{
  const messages = [freshMsg("user", "我去 弄 材料", new Date(NOW.getTime() - 60 * 60_000))];
  const r = detectAnyTrigger({
    now: NOW,
    messages,
    journalText: "上会材料 5/7 前完成",
    lastFocusLeftTs: NOW.getTime() - 50 * 60_000,
    lastFocusReturnTs: NOW.getTime() - 30_000,
    firedLog: [],
    lastDismissTs: null,
  });
  check("deadline 优先 silence/focus", r != null && r.kind === "deadline", JSON.stringify(r));
}

console.log(`\n${pass} pass, ${fail} fail`);
if (fail > 0) process.exit(1);
