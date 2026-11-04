// @vitest-environment jsdom
/** 市场「📚 知识库」页签 (11/4 鸿波「为什么知识库的分享没有?」)。数据全是虚构的。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { WikiDocDetail, WikiDocSummary } from "../lib/wiki";
import { useAuthStore } from "../store/auth";
import { WikiMarketPage } from "./WikiMarketPage";

const docs: WikiDocSummary[] = [
  { namespace: "dept/研发部", file_id: "a1", filename: "wiki/concepts/发版流程.md", title: "发版流程",
    kind: "concept", description_preview: "打包前先跑全部测试", published_by: "alice@example.com",
    published_at: "2026-11-01T08:00:00Z", updated_at: "2026-11-02T08:00:00Z" },
  { namespace: "dept/研发部", file_id: "b2", filename: "wiki/entities/旧客户.md", title: "旧客户",
    kind: "entity", published_by: "bob@example.com", published_at: "2026-10-01T08:00:00Z",
    stale_after_unpublish: true, unpublished_at: "2026-10-05T08:00:00Z" },
];
const unpublish = vi.fn(async () => ({ ok: true }));

vi.mock("../lib/wiki", async (orig) => ({
  ...(await orig<typeof import("../lib/wiki")>()),
  listWikiDocs: async () => ({ documents: docs, count: docs.length }),
  getWikiDoc: async (dept: string, id: string): Promise<WikiDocDetail> => {
    const d = docs.find((x) => x.file_id === id && x.namespace === `dept/${dept}`);
    if (!d) throw new Error("不存在");
    return { ...d, body_md: d.stale_after_unpublish ? "" : "## 步骤\n- 先跑 **测试**" };
  },
  unpublishWikiDoc: (...a: unknown[]) => unpublish(...(a as [])),
}));

function at(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/market/wiki/*" element={<WikiMarketPage />} />
      </Routes>
    </MemoryRouter>,
  );
}
const me = (email: string, role: "employee" | "admin" | "sysadmin" = "employee") =>
  useAuthStore.getState().setMe({ email, department: "研发部", role, managed_departments: [],
    auth_method: "password", must_change_password: false });

beforeEach(() => unpublish.mockClear());
afterEach(cleanup);

describe("知识库页签", () => {
  it("按部门列出, 撤回的标「已撤回」, 链接带中文部门", async () => {
    me("carol@example.com");
    at("/market/wiki");
    expect(await screen.findByText("发版流程")).toBeTruthy();
    expect(screen.getByText(/知识库分享 · 1 条 \/ 1 个部门/)).toBeTruthy();
    expect(screen.getByText("已撤回")).toBeTruthy();
    const link = screen.getByText("发版流程").closest("a")!;
    expect(decodeURIComponent(link.getAttribute("href")!)).toBe("/market/wiki/研发部/a1");
  });

  it("搜索按作者过滤", async () => {
    me("carol@example.com");
    at("/market/wiki");
    await screen.findByText("发版流程");
    fireEvent.change(screen.getByPlaceholderText(/搜标题/), { target: { value: "bob@" } });
    expect(screen.queryByText("发版流程")).toBeNull();
    expect(screen.getByText("旧客户")).toBeTruthy();
  });

  it("详情显示正文; 别人的条目没有撤回按钮", async () => {
    me("carol@example.com");
    at(`/market/wiki/${encodeURIComponent("研发部")}/a1`);
    expect(await screen.findByText("步骤")).toBeTruthy();
    expect(screen.queryByText("撤回分享")).toBeNull();
  });

  it("原作者能撤回, 带上原因", async () => {
    me("alice@example.com");
    at(`/market/wiki/${encodeURIComponent("研发部")}/a1`);
    fireEvent.click(await screen.findByText("撤回分享"));
    fireEvent.change(screen.getByPlaceholderText(/撤回原因/), { target: { value: "内容过时" } });
    fireEvent.click(screen.getByRole("button", { name: "撤回" }));
    await waitFor(() => expect(unpublish).toHaveBeenCalledWith("研发部", "a1", "内容过时"));
  });

  it("sysadmin 也能撤回别人的; 已撤回的不再显示按钮", async () => {
    me("root@example.com", "sysadmin");
    at(`/market/wiki/${encodeURIComponent("研发部")}/a1`);
    expect(await screen.findByText("撤回分享")).toBeTruthy();
    cleanup();
    at(`/market/wiki/${encodeURIComponent("研发部")}/b2`);
    expect(await screen.findByText(/原作者已于 2026-10-05 撤回/)).toBeTruthy();
    expect(screen.queryByText("撤回分享")).toBeNull();
  });
});
