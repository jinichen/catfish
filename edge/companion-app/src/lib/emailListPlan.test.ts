import { describe, expect, it } from "vitest";

import { _setLastFullAtForTest, FULL_REFRESH_MS, INCREMENTAL_LIMIT, mergeNewest, planListFetch } from "./emailListPlan";

describe("邮件列表增量刷新 (10/31)", () => {
  it("手里有列表、收件箱全部视图、半小时内 → 只拉最新一小批", () => {
    _setLastFullAtForTest(Date.now());
    const p = planListFetch({ hasItems: true, inboxAll: true, maxLimit: 500 });
    expect(p).toEqual({ incremental: true, limit: INCREMENTAL_LIMIT, sentLimit: INCREMENTAL_LIMIT });
  });

  it("没有列表 / 手动收信 / 超过半小时 / 只看未读 → 全量", () => {
    _setLastFullAtForTest(Date.now());
    expect(planListFetch({ hasItems: false, inboxAll: true, maxLimit: 500 }).incremental).toBe(false);
    expect(planListFetch({ hasItems: true, full: true, inboxAll: true, maxLimit: 500 }).incremental).toBe(false);
    expect(planListFetch({ hasItems: true, inboxAll: false, maxLimit: 500 }).incremental).toBe(false);
    _setLastFullAtForTest(Date.now() - FULL_REFRESH_MS - 1);
    expect(planListFetch({ hasItems: true, inboxAll: true, maxLimit: 500 })).toEqual({ incremental: false, limit: 500, sentLimit: 200 });
  });

  it("合并: 新的覆盖同 id (已读状态更新), 按时间新→旧, 截到上限", () => {
    const prev = [
      { id: "a", date: "2026-10-30T10:00:00Z", is_read: false },
      { id: "b", date: "2026-10-29T10:00:00Z" },
      { id: "c", date: "2026-10-28T10:00:00Z" },
    ];
    const fresh = [
      { id: "n", date: "2026-10-31T09:00:00Z" },
      { id: "a", date: "2026-10-30T10:00:00Z", is_read: true },
    ];
    const got = mergeNewest(prev, fresh, 3);
    expect(got.map((m) => m.id)).toEqual(["n", "a", "b"]);
    expect((got[1] as { is_read?: boolean }).is_read).toBe(true);
    expect(mergeNewest(undefined, fresh, 10).length).toBe(2);
  });
});
