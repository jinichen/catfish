// @vitest-environment jsdom
/** 提醒条状态 (11/07): 挂 / 稍后 / 关 / 标完成 / 排序 / 重启后还在。 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  briefingReminder,
  clampOutOfQuietHours,
  deadlineReminder,
  dueLabel,
  nextLocalMidnight,
  snoozeOptions,
  tomorrowNineAm,
  urgentEmailReminder,
  visibleReminders,
  type Reminder,
} from "../lib/reminders";
import { PROACTIVE_DISMISS_TS_KEY } from "../lib/triggers";
import { installMemoryLocalStorage } from "../lib/__fixtures__/memoryLocalStorage";
import { reloadRemindersForTest, useRemindersStore } from "./reminders";

const ls = installMemoryLocalStorage();

const NOW = new Date(2026, 10, 7, 10, 0); // 11/7 周六 10:00
const T = NOW.getTime();

function deadline(text: string, dueIso: string, extra: Partial<Parameters<typeof deadlineReminder>[0]> = {}): Reminder {
  const r = deadlineReminder({ text, due_date_iso: dueIso, task_id: `t-${text}`, ...extra }, `deadline:${text}`, NOW);
  if (!r) throw new Error("deadlineReminder 返了 null");
  return r;
}

beforeEach(() => {
  localStorage.clear();
  reloadRemindersForTest();
});

describe("待办提醒的字", () => {
  it("写清哪件事、哪天几点到期、来自哪个清单 —— 不是 LLM 改写的那句", () => {
    const r = deadline("11/9 材料闭环", "2026-11-08T18:00:00", { section: "资质集采二期", body: "等法务盖章" });
    expect(r.text).toBe("待办「11/9 材料闭环」明天 18:00 到期");
    expect(r.meta).toBe("来自 资质集采二期");
    expect(r.actionLabel).toBe("让小鲶看看还差啥");
    expect(r.chatTitle).toBe("待办：11/9 材料闭环");
    // 新对话第一句: 说明是从提醒点进来的, 带上截止 / 清单 / 备注 / task_id
    expect(r.prompt).toContain("从待办到期提醒点进来的");
    expect(r.prompt).toContain("截止：2026-11-08 18:00");
    expect(r.prompt).toContain("清单：资质集采二期");
    expect(r.prompt).toContain("备注：等法务盖章");
    expect(r.prompt).toContain("task_id：t-11/9 材料闭环");
    expect(r.task).toEqual({ taskId: "t-11/9 材料闭环", title: "11/9 材料闭环", dueIso: "2026-11-08T18:00:00" });
    // 会话列表会把「⏰ 」开头的标题当自动触发会话藏起来 (ChatSidebar AUTO_TRIGGER_PATTERNS)
    expect(r.chatTitle.startsWith("⏰")).toBe(false);
  });

  it("默认清单名不算来源, 有备注就写备注; 只有日期就不写钟点", () => {
    const r = deadline("交周报", "2026-11-07T00:00:00", { section: "Reminders", body: "  给王总\n抄送 " });
    expect(r.text).toBe("待办「交周报」今天到期");
    expect(r.meta).toBe("备注：给王总 抄送");
    expect(dueLabel(new Date("2026-11-09T09:30:00"), NOW)).toBe("后天（11/9） 9:30");
    expect(dueLabel(new Date("2026-11-10T00:00:00"), NOW)).toBe("3 天后（11/10）");
  });

  it("截止日当天结束就过期", () => {
    const r = deadline("A", "2026-11-08T18:00:00");
    expect(new Date(r.expiresAt).getDate()).toBe(8);
    expect(new Date(r.expiresAt).getHours()).toBe(23);
  });

  it("稍后选项: 2 小时后 / 明天 9 点; 到那时已过期的不给", () => {
    const tomorrow = deadline("明天的", "2026-11-08T18:00:00");
    expect(snoozeOptions(tomorrow, T).map((o) => o.label)).toEqual(["2 小时后", "明天上午 9 点"]);
    const today = deadline("今天的", "2026-11-07T18:00:00");
    expect(snoozeOptions(today, T).map((o) => o.label)).toEqual(["2 小时后"]);
    // 晚上 9 点「2 小时后」= 23 点, 落进免打扰 → 顺延到次日 9 点
    const late = new Date(2026, 10, 7, 21, 0).getTime();
    expect(clampOutOfQuietHours(late + 2 * 3600_000)).toBe(tomorrowNineAm(late));
  });

  it("急邮件: 几封、谁发的、什么主题; 邮件元数据按不可信数据包起来", () => {
    const r = urgentEmailReminder(
      [{ sender: "张三 <z@x.com>", subject: "合同今天签" }, { sender: "li@x.com", subject: "报价" }],
      ["m1", "m2"],
      NOW,
    )!;
    expect(r.text).toBe("2 封急邮件");
    expect(r.meta).toBe("张三 — 合同今天签 等");
    expect(r.actionLabel).toBe("让小鲶看看");
    expect(r.prompt).toContain('email_id: "m1"');
    expect(r.prompt).toContain("不可信数据");
  });

  it("早安播报: 「早安播报」+ 摘要, 主按钮「展开看看」", () => {
    const r = briefingReminder("今天 3 件事: 合同、报价、周会", NOW);
    expect(r.text).toBe("早安播报");
    expect(r.meta).toBe("今天 3 件事: 合同、报价、周会");
    expect(r.actionLabel).toBe("展开看看");
    expect(r.prompt).toContain("今天 3 件事: 合同、报价、周会");
  });
});

describe("排序: 最急的在前", () => {
  it("今天到期 > 急邮件 > 明后天到期 > 早安 > 闲聊; 同类按截止先后", () => {
    const items = [
      briefingReminder("早", NOW),
      deadline("后天", "2026-11-09T10:00:00"),
      urgentEmailReminder([{ sender: "a", subject: "b" }], ["m"], NOW)!,
      deadline("明天", "2026-11-08T10:00:00"),
      deadline("今天", "2026-11-07T17:00:00"),
    ];
    expect(visibleReminders(items, T).map((r) => r.id)).toEqual([
      "deadline:今天",
      "email:m",
      "deadline:明天",
      "deadline:后天",
      "briefing:2026-11/7",
    ]);
  });
});

describe("store", () => {
  it("挂上就显示; 同一条重复到达不重复挂", () => {
    const s = useRemindersStore.getState();
    expect(s.add(deadline("A", "2026-11-08T18:00:00"), T)).toBe(true);
    expect(s.add(deadline("A", "2026-11-08T18:00:00"), T)).toBe(false);
    expect(visibleReminders(useRemindersStore.getState().items, T)).toHaveLength(1);
  });

  it("稍后: 到点前不显示, 到点自己冒出来; 稍后中的也不重复挂", () => {
    const s = useRemindersStore.getState();
    s.add(deadline("A", "2026-11-08T18:00:00"), T);
    s.snooze("deadline:A", T + 2 * 3600_000);
    expect(visibleReminders(useRemindersStore.getState().items, T + 3600_000)).toHaveLength(0);
    expect(s.add(deadline("A", "2026-11-08T18:00:00"), T + 3600_000)).toBe(false);
    expect(visibleReminders(useRemindersStore.getState().items, T + 2 * 3600_000 + 1)).toHaveLength(1);
  });

  it("✕: 收起, 今天不再挂这条 (明天可以), 并记下关掉的时刻给触发器静默 30 分钟", () => {
    const s = useRemindersStore.getState();
    s.add(deadline("A", "2026-11-08T18:00:00"), T);
    s.dismiss("deadline:A", T);
    expect(useRemindersStore.getState().items).toHaveLength(0);
    expect(localStorage.getItem(PROACTIVE_DISMISS_TS_KEY)).toBe(String(T));
    expect(s.add(deadline("A", "2026-11-08T18:00:00"), T + 3600_000)).toBe(false);
    expect(s.activeKeys(T + 3600_000).has("deadline:A")).toBe(true);
    expect(s.add(deadline("A", "2026-11-08T18:00:00"), nextLocalMidnight(T) + 1)).toBe(true);
  });

  it("已完成: 收起, 30 天内不再挂", () => {
    const s = useRemindersStore.getState();
    s.add(deadline("A", "2026-11-08T18:00:00"), T);
    s.markDone("deadline:A", T);
    expect(useRemindersStore.getState().items).toHaveLength(0);
    expect(s.add(deadline("A", "2026-11-08T18:00:00"), T + 3 * 86400_000)).toBe(false);
  });

  it("点了主按钮: 收起, 今天不再挂", () => {
    const s = useRemindersStore.getState();
    s.add(deadline("A", "2026-11-08T18:00:00"), T);
    s.markStarted("deadline:A", T);
    expect(useRemindersStore.getState().items).toHaveLength(0);
    expect(s.add(deadline("A", "2026-11-08T18:00:00"), T + 60_000)).toBe(false);
  });

  it("持久化: 重启后挂着的、稍后中的、处理过的都还在; 过期的清掉", () => {
    // 重启加载时按「现在」清过期的: 现在也钉在 T (11/08 起真实时间过了 T 当天午夜, 「今天不再提醒」那条被当过期清掉)
    vi.useFakeTimers();
    vi.setSystemTime(T);
    const s = useRemindersStore.getState();
    s.add(deadline("A", "2026-11-08T18:00:00"), T);
    s.add(deadline("B", "2026-11-09T18:00:00"), T);
    s.snooze("deadline:B", tomorrowNineAm(T));
    s.add(deadline("C", "2026-11-09T18:00:00"), T);
    s.dismiss("deadline:C", T);
    useRemindersStore.setState({ items: [], handled: {}, notifiedAt: null });
    reloadRemindersForTest();
    const after = useRemindersStore.getState();
    expect(after.items.map((r) => r.id).sort()).toEqual(["deadline:A", "deadline:B"]);
    expect(after.items.find((r) => r.id === "deadline:B")?.snoozedUntil).toBe(tomorrowNineAm(T));
    expect(after.handled["deadline:C"]).toBe(nextLocalMidnight(T));
    vi.useRealTimers();
  });

  it("localStorage 坏数据 / 写不进不报错", () => {
    localStorage.setItem("catfish:reminders:v1", "{not json");
    expect(() => reloadRemindersForTest()).not.toThrow();
    expect(useRemindersStore.getState().items).toEqual([]);
    const orig = ls.setItem;
    ls.setItem = () => {
      throw new Error("quota");
    };
    try {
      expect(useRemindersStore.getState().add(deadline("A", "2026-11-08T18:00:00"), T)).toBe(true);
      expect(useRemindersStore.getState().items).toHaveLength(1);
    } finally {
      ls.setItem = orig;
    }
  });

  it("发过通知后 30 分钟内切回来 → 落到工作台 (只认一次); 过了窗口或没提醒挂着不算", () => {
    const s = useRemindersStore.getState();
    s.add(deadline("A", "2026-11-08T18:00:00"), T);
    s.noteNotified(T);
    expect(s.consumeNotifyReturn(T + 60_000)).toBe(true);
    expect(s.consumeNotifyReturn(T + 120_000)).toBe(false);
    s.noteNotified(T);
    expect(s.consumeNotifyReturn(T + 31 * 60_000)).toBe(false);
    s.dismiss("deadline:A", T);
    s.noteNotified(T);
    expect(s.consumeNotifyReturn(T + 60_000)).toBe(false);
  });
});
