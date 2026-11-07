/** 提醒条 · 派给同事的任务 (11/07): 逾期 (发起人) / 快到截止 (收件人) / 延期选项 / 了结了就收掉。 */
import { describe, expect, it } from "vitest";

import { settledReminder } from "./colleagueTasksStore";
import { colleagueDueSoonReminder, colleagueOverdueReminder, extendOptions, visibleReminders } from "./reminders";
import type { TaskList } from "./tauri_colleague_tasks";

// 2026-11-07 周六 15:30
const NOW = new Date(2026, 10, 7, 15, 30);

describe("逾期提醒 (发起人)", () => {
  it("名单、截止都写在条上; 主按钮是催一下", () => {
    const r = colleagueOverdueReminder(
      { task_id: "t_1", title: "整理拜访记录", deadline: "2026-11-07T12:00", people: ["zhang@example.com", "li@example.com"] }, NOW)!;
    expect(r.text).toBe("「整理拜访记录」过了截止，zhang、li 还没交");
    expect(r.meta).toBe("截止 今天 12:00");
    expect(r).toMatchObject({ kind: "ctask", actionLabel: "催一下", ctask: { taskId: "t_1", role: "owner", deadline: "2026-11-07T12:00" } });
    expect(r.id).toBe("ctask-overdue:t_1:2026-11-07T12:00");
  });
  it("人多只列三个", () => {
    const r = colleagueOverdueReminder({ task_id: "t_1", title: "x", deadline: "2026-11-07", people: ["a@x.com", "b@x.com", "c@x.com", "d@x.com"] }, NOW)!;
    expect(r.text).toContain("a、b、c 等 4 人");
  });
  it("没名单 / 没截止: 不挂", () => {
    expect(colleagueOverdueReminder({ task_id: "t_1", title: "x", deadline: "2026-11-07", people: [] }, NOW)).toBeNull();
    expect(colleagueOverdueReminder({ task_id: "t_1", title: "x", deadline: null, people: ["a@x.com"] }, NOW)).toBeNull();
  });
});

describe("快到截止 (收件人)", () => {
  it("还没接的叫「去看看」, 在做的叫「去交回」; 过了截止自己消失", () => {
    const r = colleagueDueSoonReminder({ task_id: "t_2", title: "周报", from: "周鸿 <zhou@example.com>", deadline: "2026-11-07T17:00", state: "待处理" }, NOW)!;
    expect(r.text).toBe("「周报」今天 17:00 截止，还没交回");
    expect(r.meta).toBe("周鸿 派的 · 还没接");
    expect(r.actionLabel).toBe("去看看");
    expect(r.expiresAt).toBe(new Date(2026, 10, 7, 17, 0).getTime());
    expect(colleagueDueSoonReminder({ task_id: "t_2", title: "周报", from: "zhou@example.com", deadline: "2026-11-07T17:00", state: "在做" }, NOW)!.actionLabel).toBe("去交回");
    expect(colleagueDueSoonReminder({ task_id: "t_2", title: "周报", from: "z", deadline: "2026-11-07T15:00" }, NOW)).toBeNull();
  });
  it("跟今天到期的待办一样排在最前", () => {
    const r = colleagueDueSoonReminder({ task_id: "t_2", title: "周报", from: "z", deadline: "2026-11-07T17:00" }, NOW)!;
    const email = { ...r, id: "email:1", kind: "email" as const, ctask: undefined, createdAt: NOW.getTime() + 1 };
    expect(visibleReminders([email, r], NOW.getTime())[0].id).toBe(r.id);
  });
});

describe("延期选项", () => {
  it("下午三点半: 今天 18:00 / 明天 / 后天 / 下周一", () => {
    expect(extendOptions(NOW)).toEqual([
      { label: "今天 18:00", deadline: "2026-11-07T18:00" },
      { label: "明天 18:00", deadline: "2026-11-08T18:00" },
      { label: "后天 18:00", deadline: "2026-11-09T18:00" },
    ]);   // 后天就是下周一, 不重复
  });
  it("傍晚就不给「今天」; 周三给下周一", () => {
    const wed = new Date(2026, 10, 4, 17, 30);
    expect(extendOptions(wed).map((o) => o.label)).toEqual(["明天 18:00", "后天 18:00", "下周一 18:00"]);
    expect(extendOptions(wed)[2].deadline).toBe("2026-11-09T18:00");
  });
});

describe("了结了就收掉", () => {
  const owner = colleagueOverdueReminder({ task_id: "t_1", title: "x", deadline: "2026-11-07T12:00", people: ["a@x.com"] }, NOW)!;
  const recip = colleagueDueSoonReminder({ task_id: "t_2", title: "y", from: "z", deadline: "2026-11-07T17:00" }, NOW)!;
  const list = (o: Partial<TaskList["outgoing"][number]>, i: Partial<TaskList["incoming"][number]>): TaskList => ({
    outgoing: [{ task_id: "t_1", title: "x", status: "open", deadline: "2026-11-07T12:00", created_at: "", schedule: null,
                 progress: { "超时未交": 1 }, recipients: 1, ...o }],
    incoming: [{ task_id: "t_2", title: "y", owner: "z", deadline: "2026-11-07T17:00", state: "在做", late: false,
                 received_at: "", reminded_at: null, schedule: null, ...i } as TaskList["incoming"][number]],
    pipelines: [],
  });
  it("还有人没交 / 还没交回: 留着", () => {
    expect(settledReminder(owner, list({}, {}))).toBe(false);
    expect(settledReminder(recip, list({}, {}))).toBe(false);
  });
  it("都交了 / 延期了 / 取消了: 收掉", () => {
    expect(settledReminder(owner, list({ progress: { "逾期完成": 1 } }, {}))).toBe(true);
    expect(settledReminder(owner, list({ deadline: "2026-11-08T18:00" }, {}))).toBe(true);
    expect(settledReminder(owner, list({ status: "cancelled" }, {}))).toBe(true);
  });
  it("收件人交回了 / 截止改了: 收掉", () => {
    expect(settledReminder(recip, list({}, { state: "已交回" }))).toBe(true);
    expect(settledReminder(recip, list({}, { deadline: "2026-11-08T18:00" }))).toBe(true);
  });
});
