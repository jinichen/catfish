// @vitest-environment jsdom
/** 配额日志「导出 CSV」导出查询条件下的全部记录, 不是当前页 50 条 (11/02 鸿波)。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AuditEvent, AuditEventsParams } from "../../lib/me";

const TOTAL = 258;
const calls: AuditEventsParams[] = [];
const ev = (i: number): AuditEvent => ({
  ts: 1793600000 + i, user: "a@x.cn", department: "eng", model: "m", prompt_tokens: i,
  completion_tokens: 1, total_tokens: i + 1, latency_ms: 10, status: "ok",
} as AuditEvent);

vi.mock("../../lib/me", async (orig) => ({
  ...(await orig<typeof import("../../lib/me")>()),
  fetchAuditEvents: async (p: AuditEventsParams) => {
    calls.push(p);
    const off = p.offset ?? 0;
    const n = Math.max(0, Math.min(p.limit ?? 50, TOTAL - off));
    return { ok: true, data: { events: Array.from({ length: n }, (_, i) => ev(off + i)), total: TOTAL, limit: p.limit, offset: off } };
  },
}));

afterEach(cleanup);

describe("配额日志导出", () => {
  it("按上次查询条件逐页拉全部", async () => {
    let csv = "";
    URL.createObjectURL = (b: Blob) => {
      const r = new FileReader(); // jsdom 的 Blob 没有 .text()
      r.onload = () => { csv = String(r.result); };
      r.readAsText(b);
      return "blob:x";
    };
    URL.revokeObjectURL = () => undefined;
    HTMLAnchorElement.prototype.click = () => undefined; // jsdom 不会真下载
    const { AdminQuotaEvents } = await import("./QuotaEventsPage");
    render(<MemoryRouter><AdminQuotaEvents /></MemoryRouter>);
    const btn = await screen.findByRole("button", { name: "导出 CSV" });
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false));
    calls.length = 0;
    fireEvent.click(btn);
    await waitFor(() => expect(csv.split("\n")).toHaveLength(TOTAL + 1));
    expect(calls.map((c) => c.offset)).toEqual([0, 200]);
    expect(calls.every((c) => c.limit === 200 && c.since_ms != null)).toBe(true);
  });
});
