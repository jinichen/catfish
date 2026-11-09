// @vitest-environment jsdom
/** 连接器市场 · 员工提交 + 审核 (11/08 分享治理 §3.4)。数据全是虚构的。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { McpSubmission, McpSubmitBody } from "../lib/mcp";
import { useAuthStore } from "../store/auth";
import { McpMarketPage } from "./McpMarketPage";

const pending: McpSubmission = {
  id: "crm-readonly", name: "CRM 只读", version: "0.1.0", description: "查客户", auth_type: "api_key", tools: ["crm_find_customer"],
  scope: "department", departments: ["销售部"], trial_credentials: "找 IT 小王要测试账号", submitted_by: "dave@example.com",
  submitted_dept: "销售部", submitted_at: "2026-11-08T01:00:00Z", submit_note: "销售查客户用", status: "pending",
};
const submitConnector = vi.fn(async (b: McpSubmitBody) => ({ ok: true, submission: { ...pending, id: "x", name: b.scope } }));
const reviewSubmission = vi.fn(async () => ({ ok: true, submission: { ...pending, status: "approved" as const } }));

vi.mock("../lib/mcp", async (orig) => ({
  ...(await orig<typeof import("../lib/mcp")>()),
  listConnectors: async () => [],
  listMySubmissions: async () => ({ submissions: [{ ...pending, status: "rejected", review_note: "密钥写死了", reviewed_by: "boss@example.com" }] }),
  listReviewSubmissions: async () => ({ submissions: [pending] }),
  getSubmission: async () => ({ submission: pending, manifest_yaml: "id: crm-readonly\nname: CRM 只读\n" }),
  submitConnector: (b: McpSubmitBody) => submitConnector(b),
  reviewSubmission: (...a: unknown[]) => reviewSubmission(...(a as [])),
}));

function at(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/market/mcp/*" element={<McpMarketPage />} />
      </Routes>
    </MemoryRouter>,
  );
}
const me = (role: "employee" | "manager" = "employee") =>
  useAuthStore.getState().setMe({ email: "dave@example.com", department: "销售部", role,
    managed_departments: role === "manager" ? ["销售部"] : [], auth_method: "password", must_change_password: false });

beforeEach(() => { submitConnector.mockClear(); reviewSubmission.mockClear(); });
afterEach(cleanup);

describe("连接器 · 提交与审核", () => {
  it("提交表单: 部门范围默认自己的部门, 发的是 §6.4 的字段", async () => {
    me();
    at("/market/mcp/submit");
    const yaml = await screen.findByPlaceholderText(/^id: crm-readonly/);
    const btn = screen.getByRole("button", { name: "提交审核" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    fireEvent.change(yaml, { target: { value: "id: crm\nname: CRM\n" } });
    fireEvent.change(screen.getByPlaceholderText(/它接的是哪个系统/), { target: { value: "销售查客户" } });
    fireEvent.change(screen.getByPlaceholderText(/测试环境地址/), { target: { value: "找 IT 要" } });
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    await waitFor(() => expect(submitConnector).toHaveBeenCalledTimes(1));
    expect(submitConnector.mock.calls[0][0]).toEqual({
      manifest_yaml: "id: crm\nname: CRM\n", scope: "department", departments: ["销售部"], note: "销售查客户", trial_credentials: "找 IT 要",
    });
    expect(await screen.findByText(/等部门主管审核通过/)).toBeTruthy();
  });

  it("全公司范围不带部门; 多个部门按逗号拆", async () => {
    me();
    at("/market/mcp/submit");
    fireEvent.change(await screen.findByPlaceholderText(/^id: crm-readonly/), { target: { value: "id: a\n" } });
    fireEvent.change(screen.getByPlaceholderText("销售部"), { target: { value: "销售部, 市场部" } });
    fireEvent.click(screen.getByRole("button", { name: "提交审核" }));
    await waitFor(() => expect(submitConnector).toHaveBeenCalledTimes(1));
    expect(submitConnector.mock.calls[0][0].departments).toEqual(["销售部", "市场部"]);

    fireEvent.click(screen.getByLabelText(/全公司/));
    expect(screen.queryByPlaceholderText("销售部")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "提交审核" }));
    await waitFor(() => expect(submitConnector).toHaveBeenCalledTimes(2));
    expect(submitConnector.mock.calls[1][0]).toMatchObject({ scope: "company", departments: [] });
  });

  it("列表页: 我提交的带驳回原因; 主管的「待我审核」弹窗显示清单原文、测试账号, 通过调 reviewSubmission", async () => {
    me("manager");
    at("/market/mcp");
    // 11/09 跟控制台统一: 我提交的 / 待我审核 是列表页的两个分段
    fireEvent.click(await screen.findByRole("tab", { name: /^我提交的/ }));
    expect(await screen.findByText(/驳回原因 \(boss@example.com\)：密钥写死了/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "待我审核 1" }));
    fireEvent.click(screen.getByRole("button", { name: "审核" }));
    expect(await screen.findByText(/name: CRM 只读/)).toBeTruthy();
    expect(screen.getByText("找 IT 小王要测试账号")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "驳回" }));
    expect(await screen.findByText(/驳回要写原因/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "通过" }));
    await waitFor(() => expect(reviewSubmission).toHaveBeenCalledWith("crm-readonly", "approve", ""));
  });
});
