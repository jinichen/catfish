// @vitest-environment jsdom
/** 市场外壳 (11/09 跟控制台统一样式): 左侧分组导航, 主管看得到每类的待审数, 当前页高亮。数据全是虚构的。 */
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../store/auth";
import { MarketPage } from "./MarketPage";

vi.mock("../lib/hub", async (orig) => ({
  ...(await orig<typeof import("../lib/hub")>()),
  listSkills: async () => ({ skills: [], count: 0 }),
  listMySkills: async () => ({ skills: [] }),
  listReviewSkills: async () => ({ skills: [{ namespace: "dept/研发部", name: "a", version: "1", status: "pending" }, { namespace: "dept/研发部", name: "b", version: "1", status: "pending" }] }),
}));
vi.mock("../lib/mcp", async (orig) => ({
  ...(await orig<typeof import("../lib/mcp")>()),
  listReviewSubmissions: async () => ({ submissions: [] }),
}));
vi.mock("../lib/experts", async (orig) => ({
  ...(await orig<typeof import("../lib/experts")>()),
  listReviewExperts: async () => ({ experts: [{ id: "x", version: "1" }] }),
}));
vi.mock("../lib/wiki", async (orig) => ({
  ...(await orig<typeof import("../lib/wiki")>()),
  listReviewWikiDocs: async () => { throw new Error("老中央没有这个接口"); },
}));

const me = (role: "employee" | "manager") =>
  useAuthStore.getState().setMe({ email: "u@example.com", department: "研发部", role, managed_departments: role === "manager" ? ["研发部"] : [],
    auth_method: "password", must_change_password: false });

function at(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/market/*" element={<MarketPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(cleanup);

describe("市场外壳", () => {
  it("左侧导航: 五类都在, 没有 emoji; 当前页高亮", async () => {
    me("employee");
    at("/market/skills");
    const nav = screen.getByRole("navigation", { name: "市场导航" });
    const labels = within(nav).getAllByRole("link").map((a) => a.textContent);
    expect(labels).toEqual(["技能", "连接器", "专家包", "知识库", "组件"]);
    expect(nav.textContent).not.toMatch(/[\u{1F300}-\u{1FAFF}]/u);
    expect(within(nav).getByRole("link", { name: "技能" }).getAttribute("aria-current")).toBe("page");
    expect(await screen.findByRole("heading", { name: "技能" })).toBeTruthy();
  });

  it("主管: 导航上带每类待审数; 0 和接口挂了的不显示", async () => {
    me("manager");
    at("/market/skills");
    const nav = screen.getByRole("navigation", { name: "市场导航" });
    await waitFor(() => expect(within(nav).getByRole("link", { name: /技能/ }).textContent).toBe("技能2"));
    expect(within(nav).getByRole("link", { name: /专家包/ }).textContent).toBe("专家包1");
    expect(within(nav).getByRole("link", { name: /连接器/ }).textContent).toBe("连接器");
    expect(within(nav).getByRole("link", { name: /知识库/ }).textContent).toBe("知识库");
  });

  it("员工: 不拉待审数", async () => {
    me("employee");
    at("/market/skills");
    const nav = screen.getByRole("navigation", { name: "市场导航" });
    await screen.findByRole("heading", { name: "技能" });
    expect(within(nav).getByRole("link", { name: /专家包/ }).textContent).toBe("专家包");
  });
});
