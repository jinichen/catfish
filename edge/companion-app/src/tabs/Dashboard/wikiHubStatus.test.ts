import { describe, expect, it } from "vitest";

import { groupOf, hasHubUpdate, sortGroups, toolResultError } from "./wikiHubStatus";

describe("hasHubUpdate", () => {
  const doc = (updated_at: string | null, published_at: string | null = null) => ({
    updated_at,
    published_at,
  });

  it("没装 → 不提示", () => {
    expect(hasHubUpdate(doc("2026-09-30T02:00:00Z"), undefined)).toBe(false);
  });

  it("hub 比装的那版新 → 有更新", () => {
    expect(
      hasHubUpdate(doc("2026-09-30T02:00:00+00:00"), {
        hubUpdatedAt: "2026-09-30T01:00:00Z",
        installedAt: "2026-09-30T01:05:00Z",
      }),
    ).toBe(true);
  });

  it("装的就是最新版 → 没更新 (PG isoformat 带微秒和 +00:00 也能比)", () => {
    expect(
      hasHubUpdate(doc("2026-09-30T02:00:00.123456+00:00"), {
        hubUpdatedAt: "2026-09-30T02:00:00.123456+00:00",
        installedAt: "2026-09-30T02:10:00Z",
      }),
    ).toBe(false);
  });

  it("老副本没记 hub 版本 → 退回按本机安装时间比", () => {
    const installed = { hubUpdatedAt: "", installedAt: "2026-09-30T01:00:00Z" };
    expect(hasHubUpdate(doc("2026-09-30T02:00:00Z"), installed)).toBe(true);
    expect(hasHubUpdate(doc("2026-09-29T02:00:00Z"), installed)).toBe(false);
  });

  it("时间解析不了 → 不提示", () => {
    expect(hasHubUpdate(doc(null), { hubUpdatedAt: "", installedAt: "" })).toBe(false);
    expect(hasHubUpdate(doc("garbage"), { hubUpdatedAt: "x", installedAt: "" })).toBe(false);
  });
});

describe("toolResultError", () => {
  it("外层 ok + 工具 ok → null", () => {
    expect(toolResultError({ ok: true, result: { ok: true } })).toBeNull();
  });

  it("外层 ok 但工具失败 → 工具的 error (之前这种情况会显示'已装')", () => {
    expect(toolResultError({ ok: true, result: { ok: false, error: "不是你的部门" } })).toBe(
      "不是你的部门",
    );
  });

  it("外层失败 → 外层 error", () => {
    expect(toolResultError({ ok: false, result: null, error: "bridge down" })).toBe("bridge down");
  });
});

describe("分组 (11/09 全公司)", () => {
  it("全公司可见的单独一组排最前, 其余按部门", () => {
    expect(groupOf({ namespace: "dept/销售部", visible_to: "company" })).toBe("全公司");
    expect(groupOf({ namespace: "dept/研发部", visible_to: "department" })).toBe("研发部");
    expect(groupOf({ namespace: "dept/研发部" })).toBe("研发部");
    expect(sortGroups(["研发部", "全公司", "市场部"])).toEqual(["全公司", "市场部", "研发部"]);
  });
});
