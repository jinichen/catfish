/** 提前准备的公共部分 (11/09): 几点跑、建 / 撤 / 清、每天上限、任务名前缀。 */
import { describe, expect, it } from "vitest";

import {
  localIso,
  planPreps,
  prepLabel,
  prepRunAt,
  PREP_MAX_PER_DAY,
  PREP_SOON_MS,
  whenLabel,
  type PrepItem,
  type PrepRecord,
  type PrepSource,
} from "./advancePrep";

const at = (m: number, d: number, h: number, mi = 0) => new Date(2026, m - 1, d, h, mi).getTime();
const item = (key: string, dueMs: number, source: PrepSource = "calendar"): PrepItem =>
  ({ key, source, dueMs, name: `会前准备：${key}`, prompt: "p" });
const rec = (p: Partial<PrepRecord>): PrepRecord => ({ jobId: "j", runAt: at(11, 9, 16), start: at(11, 10, 10), day: "2026-11-08", ...p });
const ALL = new Set<PrepSource>(["calendar", "todo", "email"]);

describe("prepRunAt", () => {
  const now = at(11, 9, 10);
  it("明天到期 → 今天 16:00", () => {
    expect(prepRunAt(at(11, 10, 10), now)).toBe(at(11, 9, 16));
  });
  it("前一天 16:00 已经过了 → 2 分钟后", () => {
    expect(prepRunAt(at(11, 10, 10), at(11, 9, 17))).toBe(at(11, 9, 17) + PREP_SOON_MS);
  });
  it("今天到期 (还有 3 小时) → 2 分钟后", () => {
    expect(prepRunAt(at(11, 9, 13), now)).toBe(now + PREP_SOON_MS);
  });
  it("不到 1 小时 / 超过 4 天 → 不准备", () => {
    expect(prepRunAt(at(11, 9, 10, 40), now)).toBeNull();
    expect(prepRunAt(at(11, 14, 10), now)).toBeNull();
  });
  it("周五准备周一的会: 周日 16:00 跑", () => {
    expect(prepRunAt(at(11, 16, 10), at(11, 13, 10))).toBe(at(11, 15, 16));
  });
  it("晚上 22 点看到明早 0 点到期的: 前一天 16:00 早过了 → 2 分钟后", () => {
    expect(prepRunAt(at(11, 10, 0), at(11, 9, 22))).toBe(at(11, 9, 22) + PREP_SOON_MS);
  });
});

describe("格式", () => {
  it("hermes 的本地时间 / 显示用时间", () => {
    expect(localIso(at(11, 9, 16, 5))).toBe("2026-11-09T16:05");
    expect(whenLabel(at(11, 10, 10))).toBe("11/10 10:00");
    expect(whenLabel(at(11, 10, 0))).toBe("11/10");
  });
  it("任务名前缀 → 种类", () => {
    expect(prepLabel("会前准备：周例会（11/10 10:00）")).toEqual({ kind: "meeting", label: "周例会（11/10 10:00）" });
    expect(prepLabel("邮件回复：报价确认（11/10 18:00 前）")?.kind).toBe("emailReply");
    expect(prepLabel("群里交代：把报价单发给张三（zhang）")?.kind).toBe("roomAsk");
    expect(prepLabel("AI六巨头日报")).toBeNull();
  });
});

describe("planPreps", () => {
  const now = at(11, 9, 10);

  it("新事 → 建; 建过的不重复", () => {
    const it1 = item("a", at(11, 10, 10));
    expect(planPreps([it1], ALL, {}, now).create.map((c) => c.item.key)).toEqual(["a"]);
    expect(planPreps([it1], ALL, { a: rec({}) }, now).create).toEqual([]);
  });

  it("事情没了、还没跑 → 撤; 已经跑过的不动", () => {
    const plan = planPreps([item("other", at(11, 11, 10))], ALL,
      { old: rec({ jobId: "j1" }), ran: rec({ jobId: "j2", runAt: at(11, 9, 9) }) }, now);
    expect(plan.cancel).toEqual([{ key: "old", jobId: "j1" }]);
  });

  it("这个来源这一轮没读成 → 它的记录不撤; 别的来源照撤", () => {
    const records = { cal: rec({ jobId: "c", source: "calendar" }), mail: rec({ jobId: "m", source: "email" }) };
    const plan = planPreps([], new Set<PrepSource>(["email"]), records, now);
    expect(plan.cancel).toEqual([{ key: "mail", jobId: "m" }]);
  });

  it("第一版的记录没有 source → 当日历", () => {
    const plan = planPreps([], new Set<PrepSource>(["todo"]), { legacy: rec({}) }, now);
    expect(plan.cancel).toEqual([]);
  });

  it("到期过了的记录 → 清掉", () => {
    expect(planPreps([], ALL, { past: rec({ start: at(11, 9, 8) }) }, now).forget).toEqual(["past"]);
  });

  it(`一天最多新建 ${PREP_MAX_PER_DAY} 条 (今天已经建了 3 条 → 只再建 2 条, 先建最急的)`, () => {
    const items = [14, 11, 13, 12].map((h) => item(`h${h}`, at(11, 10, h), "todo"));
    const today = Object.fromEntries([1, 2, 3].map((i) => [`t${i}`, rec({ day: "2026-11-09", start: at(11, 12, 10), runAt: at(11, 9, 9) })]));
    expect(planPreps(items, ALL, today, now).create.map((c) => c.item.key)).toEqual(["h11", "h12"]);
  });
});

describe("外部文字", () => {
  it("去掉 hermes 会拒的隐形字符 (零宽空格、方向控制符)", async () => {
    const { clip } = await import("./advancePrep");
    expect(clip("周​例会‮﻿", 20)).toBe("周例会");
  });
  it("hermes 拒过的不算今天的额度, 撤的时候只清记录", () => {
    const now = at(11, 9, 10);
    const rejected = { jobId: "", runAt: at(11, 9, 16), start: at(11, 10, 10), day: "2026-11-09", rejected: true };
    const items = [1, 2, 3, 4, 5].map((h) => item(`k${h}`, at(11, 10, 10 + h)));
    expect(planPreps(items, ALL, { r: rejected }, now).create).toHaveLength(PREP_MAX_PER_DAY);
    expect(planPreps(items, ALL, { r: rejected }, now).cancel).toEqual([{ key: "r", jobId: "" }]);
  });
});
