// @vitest-environment jsdom
/** 11/14: 共享知识页历史版本 —— 展开才去拿; 能看某一版; 作者能恢复 (要点两次), 同事没有恢复按钮 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fetchWithAuth = vi.fn();
vi.mock("../../lib/me", () => ({ fetchWithAuth: (...a: unknown[]) => fetchWithAuth(...a) }));
vi.mock("../../lib/env", () => ({ config: { gatewayUrl: "http://gw" } }));

import WikiHistory from "./WikiHistory";

const ok = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
const BASE = "http://gw/v1/wiki/documents/dept/%E7%A0%94%E5%8F%91%E9%83%A8/abc";

beforeEach(() => {
  fetchWithAuth.mockReset();
  fetchWithAuth.mockImplementation((url: string, init?: RequestInit) => {
    if (url === `${BASE}/history`) {
      return ok({ current_revision: 3, current_updated_at: "2026-11-14T02:00:00Z",
        versions: [{ revision: 2, published_by: "alice@x", updated_at: "2026-11-13T02:00:00Z" }, { revision: 1, updated_at: "2026-11-12T02:00:00Z" }] });
    }
    if (url === `${BASE}/history/2`) return ok({ revision: 2, body_md: "# 改之前\n\n第二版正文", frontmatter_yaml: "" });
    if (url === `${BASE}/history/2/restore` && init?.method === "POST") return ok({ revision: 4, restored_from: 2, review_status: "pending" });
    return Promise.resolve(new Response(JSON.stringify({ detail: "nope" }), { status: 404 }));
  });
});
afterEach(cleanup);

describe("WikiHistory", () => {
  it("展开才去拿; 能看某一版的正文", async () => {
    render(<WikiHistory namespace="dept/研发部" fileId="abc" canRestore={false} />);
    expect(fetchWithAuth).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("历史版本"));
    await waitFor(() => expect(screen.getByText(/现在是第 3 版/)).toBeTruthy());
    expect(screen.getByText("第 2 版")).toBeTruthy();
    expect(screen.queryByText("恢复成这一版")).toBeNull();
    fireEvent.click(screen.getAllByText("看")[0]);
    await waitFor(() => expect(screen.getByText("第二版正文")).toBeTruthy());
  });

  it("作者: 恢复要点两次, 恢复完说清楚本机那页没变", async () => {
    render(<WikiHistory namespace="dept/研发部" fileId="abc" canRestore />);
    fireEvent.click(screen.getByText("历史版本"));
    await waitFor(() => screen.getByText("第 2 版"));
    fireEvent.click(screen.getAllByText("恢复成这一版")[0]);
    expect(fetchWithAuth.mock.calls.some((c) => String(c[0]).endsWith("/restore"))).toBe(false);
    fireEvent.click(screen.getByText("确认恢复?"));
    await waitFor(() => expect(screen.getByText(/同事那边已回到第 2 版/)).toBeTruthy());
    expect(screen.getByText(/你本机这页没变/)).toBeTruthy();
  });

  it("中央报错把原因说出来", async () => {
    fetchWithAuth.mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ detail: "这一页已撤回, 历史版本只有作者和管理员能看" }), { status: 403 })));
    render(<WikiHistory namespace="dept/研发部" fileId="abc" canRestore={false} />);
    fireEvent.click(screen.getByText("历史版本"));
    await waitFor(() => expect(screen.getByText(/只有作者和管理员能看/)).toBeTruthy());
  });
});
