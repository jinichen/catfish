// @vitest-environment jsdom
/** 分享审计: 三个来源三种形状合成一张表 (11/08 分享治理 §2.4)。数据全是虚构的。 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/hub", async (orig) => ({
  ...(await orig<typeof import("../../lib/hub")>()),
  fetchAudit: async () => [
    { ts: "2026-11-08T03:00:00+00:00", event: "approve", line: "skill", namespace: "dept/研发部", name: "release-notes", version: "0.2.0",
      by_user: "boss@example.com", note: "看过没问题" },
    { ts: "2026-11-08T01:00:00+00:00", event: "trial_result", line: "expert", id: "sales-helper", version: "1.0.0", by: "boss@example.com", ok: false },
  ],
}));
vi.mock("../../lib/mcp", async (orig) => ({
  ...(await orig<typeof import("../../lib/mcp")>()),
  fetchMcpAudit: async () => [
    { line: "mcp", ts: "2026-11-08T02:00:00", user_sub: "dave@example.com", connector_id: "crm-readonly", action: "review_reject",
      meta: { note: "密钥写死了" } },
  ],
}));
vi.mock("../../lib/api", () => ({
  api: {
    get: async (path: string) => {
      if (path.startsWith("/v1/wiki/audit")) {
        return { events: [{ ts: "2026-11-08T04:00:00+00:00", event: "review_withdraw", namespace: "dept/研发部", file_id: "a1",
          title: "发版流程", by_user: "boss@example.com", note: "内容过时" }] };
      }
      throw new Error("unexpected " + path);
    },
  },
}));

afterEach(cleanup);

describe("分享审计", () => {
  it("合并三条线、按时间倒序、事件翻成中文", async () => {
    const { eventLabel, mergeShareAudit } = await import("./ShareAudit");
    expect(eventLabel("submit")).toBe("提交");
    expect(eventLabel("review_approve")).toBe("看过");
    expect(eventLabel("review_withdraw")).toBe("撤下");
    expect(eventLabel("subscribe_trial")).toBe("试用订阅");
    expect(eventLabel("whatever")).toBe("whatever");

    const rows = mergeShareAudit(
      [{ ts: "2026-01-02T00:00:00Z", event: "submit", namespace: "dept/a", name: "s", version: "1", by_user: "u", scope: "department", departments: ["a"] }],
      [{ ts: "2026-01-03T00:00:00Z", event: "publish", namespace: "dept/a", file_id: "f", title: "T", by_user: "w" }],
      [{ ts: "2026-01-01T00:00:00Z", user_sub: "m", connector_id: "c", action: "subscribe_trial", meta: { trial: true } }],
    );
    expect(rows.map((r) => r.line)).toEqual(["wiki", "skill", "mcp"]);
    expect(rows[1]).toMatchObject({ who: "u", object: "dept/a/s@1", note: "部门 a" });
    expect(rows[0].object).toBe("T (dept/a)");
    expect(rows[2].note).toBe("试用");
  });

  it("页面渲染四条线的行, 专家包用 by / id", async () => {
    const { ShareAudit } = await import("./ShareAudit");
    render(<ShareAudit />);
    expect(await screen.findByText("撤下")).toBeTruthy();
    expect(screen.getByText("通过")).toBeTruthy();
    expect(screen.getByText("试用结果")).toBeTruthy();
    expect(screen.getByText("驳回")).toBeTruthy();
    expect(screen.getByText("sales-helper@1.0.0")).toBeTruthy();
    expect(screen.getByText("dept/研发部/release-notes@0.2.0")).toBeTruthy();
    expect(screen.getByText("失败")).toBeTruthy();
    expect(screen.getAllByText("boss@example.com").length).toBe(3);
    // 筛选页签带数量
    expect(screen.getByText("知识页 1")).toBeTruthy();
    expect(screen.getByText("连接器 1")).toBeTruthy();
  });
});
