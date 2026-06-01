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

// ── BL-PROACTIVE-RUNAWAY (5/25 鸿波连发 7 条 spam 修) ───────────

console.log("\n## BL-PROACTIVE-RUNAWAY: detectDeadline 返 dedupe_key");
{
  const r = detectDeadline({
    journalText: "周一 5/26 跟省厅确认中电注册地修改",
    now: new Date(2026, 4, 25, 14, 0),  // 5/25 14:00, 距 5/26 = 1 天
  });
  check("命中 deadline", r != null && r.kind === "deadline", JSON.stringify(r));
  check("含 dedupe_key 'deadline:5/26'", r != null && r.dedupe_key === "deadline:5/26", JSON.stringify(r));
}

console.log("\n## BL-PROACTIVE-RUNAWAY: shouldStaySilent 看 dedupe_key 24h cooldown");
{
  const now = new Date(2026, 4, 25, 14, 0);
  // 12h 前 fire 过同样 deadline:5/26
  const firedLog = [
    { kind: "deadline" as const, ts: now.getTime() - 12 * 3600_000, dedupe_key: "deadline:5/26" },
  ];
  const guard = shouldStaySilent({
    now,
    firedLog,
    lastDismissTs: null,
    candidateKind: "deadline",
    candidateDedupeKey: "deadline:5/26",
  });
  check(
    "同 deadline 12h 内静默 (24h cooldown)",
    guard.silent && guard.reason.includes("content_cooldown"),
    JSON.stringify(guard),
  );
}

console.log("\n## BL-PROACTIVE-RUNAWAY: 不同 deadline 不受 content cooldown 影响");
{
  const now = new Date(2026, 4, 25, 14, 0);
  const firedLog = [
    { kind: "deadline" as const, ts: now.getTime() - 12 * 3600_000, dedupe_key: "deadline:5/26" },
  ];
  // 但同 kind 30 min cooldown 还在 — 12h 远超 30 min, 所以 same-kind 也不拦
  const guard = shouldStaySilent({
    now,
    firedLog,
    lastDismissTs: null,
    candidateKind: "deadline",
    candidateDedupeKey: "deadline:5/28",  // 不同 deadline
  });
  check(
    "不同 deadline 不被 cooldown 拦",
    !guard.silent,
    JSON.stringify(guard),
  );
}

console.log("\n## BL-PROACTIVE-RUNAWAY: MAX_PROACTIVE_PER_DAY 降到 2");
{
  const now = new Date(2026, 4, 25, 14, 0);
  // 今天已 fire 2 次
  const firedLog = [
    { kind: "deadline" as const, ts: now.getTime() - 4 * 3600_000, dedupe_key: "deadline:5/26" },
    { kind: "silence" as const, ts: now.getTime() - 2 * 3600_000, dedupe_key: "silence" },
  ];
  const guard = shouldStaySilent({
    now,
    firedLog,
    lastDismissTs: null,
    candidateKind: "focus",
    candidateDedupeKey: "focus",
  });
  check(
    "今天已 2 次 → 第 3 次被 daily_cap 拦",
    guard.silent && guard.reason.includes("daily_cap"),
    JSON.stringify(guard),
  );
}

console.log("\n## BL-PROACTIVE-RUNAWAY: detectAnyTrigger 透传 dedupe_key 给 guard");
{
  const now = new Date(2026, 4, 25, 14, 0);
  // 12h 前同 deadline 已 fire
  const firedLog = [
    { kind: "deadline" as const, ts: now.getTime() - 12 * 3600_000, dedupe_key: "deadline:5/26" },
  ];
  const r = detectAnyTrigger({
    now,
    messages: [],
    journalText: "周一 5/26 跟省厅确认中电注册地修改",  // 同样 deadline
    lastFocusLeftTs: null,
    lastFocusReturnTs: null,
    firedLog,
    lastDismissTs: null,
  });
  check(
    "同 deadline 12h 内 → detectAnyTrigger 返 null (被内容 cooldown 拦)",
    r === null,
    JSON.stringify(r),
  );
}

console.log(`\n${pass} pass, ${fail} fail`);
if (fail > 0) process.exit(1);
