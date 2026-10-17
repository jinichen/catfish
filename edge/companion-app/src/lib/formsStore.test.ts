import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const sync = vi.fn();
const list = vi.fn();
const notify = vi.fn(() => Promise.resolve());

vi.mock("./tauri_forms", () => ({ formSync: () => sync(), formList: () => list() }));
vi.mock("./tauri_app", () => ({ sendNotification: (t: string, b: string) => notify(t, b) }));

import { __resetFormsStore, getSnapshot, pendingFormCount, refreshForms } from "./formsStore";

const empty = { invite: 0, response: 0, remind: 0, close: 0, new_invites: [] };
const inc = (state: string, closed = false) => ({
  form_id: `f_20261017_${Math.random().toString(16).slice(2, 10)}`, title: "t", owner: "a@x.com",
  deadline: null, state, received_at: null, reminded_at: null, closed, fields: 3,
});

beforeEach(() => {
  __resetFormsStore();
  sync.mockReset();
  list.mockReset();
  notify.mockClear();
});
afterEach(() => __resetFormsStore());

describe("formsStore", () => {
  it("新邀请和被催都发系统通知", async () => {
    sync.mockResolvedValue({ ...empty, invite: 1, remind: 2, new_invites: [{ form_id: "f", from: "zhou@example.com", title: "10 月安全检查" }] });
    list.mockResolvedValue({ incoming: [], outgoing: [] });
    await refreshForms();
    expect(notify).toHaveBeenCalledTimes(2);
    expect(notify.mock.calls[0][1]).toContain("zhou@example.com 请你填《10 月安全检查》");
    expect(notify.mock.calls[1][1]).toContain("2 张表");
  });

  it("没新东西不打扰", async () => {
    sync.mockResolvedValue(empty);
    list.mockResolvedValue({ incoming: [], outgoing: [] });
    await refreshForms();
    expect(notify).not.toHaveBeenCalled();
  });

  it("取邮筒失败 (没登录) 列表照样刷, 错误记下来", async () => {
    sync.mockRejectedValue(new Error("还没登录鲶鱼账号"));
    list.mockResolvedValue({ incoming: [inc("待填")], outgoing: [] });
    await refreshForms();
    const s = getSnapshot();
    expect(s.loaded).toBe(true);
    expect(s.incoming).toHaveLength(1);
    expect(s.syncError).toContain("没登录");
  });

  it("红点只算还要我动手的", () => {
    const s = { incoming: [inc("待填"), inc("草稿"), inc("已提交"), inc("不填"), inc("待填", true)], outgoing: [], loaded: true, syncError: null } as never;
    expect(pendingFormCount(s)).toBe(2);
  });

  it("并发刷新合并成一次", async () => {
    sync.mockResolvedValue(empty);
    list.mockResolvedValue({ incoming: [], outgoing: [] });
    await Promise.all([refreshForms(), refreshForms(), refreshForms()]);
    expect(sync).toHaveBeenCalledTimes(1);
  });
});
