/** 派任务给同事的前端状态 (11/4): 通知、红点、取邮筒失败照样刷列表。数据全是虚构的。 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const sync = vi.fn();
const list = vi.fn();
const notify = vi.fn(() => Promise.resolve());

vi.mock("./tauri_colleague_tasks", () => ({ taskSync: () => sync(), taskList: () => list() }));
vi.mock("./tauri_app", () => ({ sendNotification: (t: string, b: string) => notify(t, b) }));

import { __resetTasksStore, getSnapshot, pendingTaskCount, refreshTasks } from "./colleagueTasksStore";

const inc = (state: string) => ({
  task_id: `t_20261104_${Math.random().toString(16).slice(2, 10)}`, title: "t", owner: "zhou@example.com",
  deadline: null, state, late: false, received_at: null, reminded_at: null,
});

beforeEach(() => {
  __resetTasksStore();
  sync.mockReset();
  list.mockReset();
  notify.mockClear();
});
afterEach(() => __resetTasksStore());

describe("colleagueTasksStore", () => {
  it("收到任务 / 被催 / 交回 / 不接发通知, 接下了不打扰", async () => {
    const ev = (kind: string) => ({ kind, task_id: "t", from: "zhang@example.com", title: "整理拜访情况" });
    sync.mockResolvedValue({ events: ["invite", "remind", "accepted", "done", "declined", "cancel"].map(ev) });
    list.mockResolvedValue({ incoming: [], outgoing: [] });
    await refreshTasks();
    expect(notify).toHaveBeenCalledTimes(5);
    expect(notify.mock.calls[0][1]).toContain("zhang@example.com 派给你一个任务「整理拜访情况」");
    expect(notify.mock.calls.map((c) => c[0])).not.toContain(undefined);
  });

  it("定时 / 周期: 待核对带期数, 改了要重新同意", async () => {
    sync.mockResolvedValue({ events: [
      { kind: "review", task_id: "t", from: "zhou@example.com", title: "周报", period: 3 },
      { kind: "changed", task_id: "t", from: "zhou@example.com", title: "周报" },
    ] });
    list.mockResolvedValue({ incoming: [], outgoing: [] });
    await refreshTasks();
    expect(notify.mock.calls[0][1]).toContain("「周报」第 3 次做好了");
    expect(notify.mock.calls[1][1]).toContain("要你重新看一下");
  });

  it("中央太旧 / 没登录: 列表照样刷, 错误记下来", async () => {
    sync.mockRejectedValue(new Error("中央端版本太旧, 还不支持派任务"));
    list.mockResolvedValue({ incoming: [inc("待处理")], outgoing: [] });
    await refreshTasks();
    const s = getSnapshot();
    expect(s.loaded).toBe(true);
    expect(s.incoming).toHaveLength(1);
    expect(s.syncError).toContain("中央端版本太旧");
  });

  it("红点只算还要我动手的 (待处理 / 在做)", () => {
    const s = { incoming: [inc("待处理"), inc("在做"), inc("已交回"), inc("不接"), inc("已取消"),
                           inc("待核对"), inc("要重新同意"), inc("已安排"), inc("进行中"), inc("已结束")], outgoing: [], loaded: true, syncError: null } as never;
    expect(pendingTaskCount(s)).toBe(4);
  });
});
