// @vitest-environment jsdom
/** 技能市场的审核 UI (11/08 分享治理 §3.1)。数据全是虚构的。 */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SkillDetail, SkillReview, SkillSummary } from "../lib/hub";
import { useAuthStore } from "../store/auth";
import { SkillsHubPage } from "./SkillsHubPage";

const pendingRec: SkillReview = {
  namespace: "dept/研发部", name: "release-notes", version: "0.2.0", scope: "department", departments: ["研发部"],
  status: "pending", submitted_by: "alice@example.com", submitted_dept: "研发部", submitted_at: "2026-11-08T02:00:00Z",
  submit_note: "自动整理发版说明", file_count: 2, bundle_sha256: "abcdef0123456789",
};
const skills: SkillSummary[] = [
  { namespace: "dept/研发部", name: "release-notes", latest_version: "0.2.0", published_by: "alice@example.com", review: pendingRec },
  { namespace: "shared", name: "old-skill", latest_version: "1.0.0", published_by: "bob@example.com",
    review: { scope: "company", departments: [], status: "approved", legacy: true } },
  { namespace: "dept/研发部", name: "bad-skill", latest_version: "0.1.0", published_by: "carol@example.com",
    review: { scope: "department", departments: ["研发部"], status: "rejected", review_note: "脚本会删文件", reviewed_by: "boss@example.com" } },
];
const reviewSkill = vi.fn(async () => ({ ...pendingRec, status: "approved" as const }));
const withdrawSkill = vi.fn(async () => ({ ...pendingRec, status: "withdrawn" as const }));

vi.mock("../lib/hub", async (orig) => ({
  ...(await orig<typeof import("../lib/hub")>()),
  listSkills: async () => ({ skills, count: skills.length }),
  listMySkills: async () => ({ skills: [pendingRec] }),
  listReviewSkills: async () => ({ skills: [pendingRec] }),
  getSkill: async (): Promise<SkillDetail> => ({
    namespace: "dept/研发部", name: "release-notes", version: "0.2.0", description: "整理发版说明", deprecated: false,
    files: ["SKILL.md", "script.py"], files_sha256: { "SKILL.md": "1234567890abcdef", "script.py": "fedcba0987654321" },
    skill_md: "---\ntitle: 发版说明\n---\n# 用法\n会打开 gitlab 看 MR",
    review: { ...pendingRec, trials: [{ by: "boss@example.com", at: "2026-11-08T03:00:00Z",
      result: { ok: true, summary: "跑通了", steps: ["拉 MR 列表", "生成说明"], files: ["notes.md"] } }] },
  }),
  reviewSkill: (...a: unknown[]) => reviewSkill(...(a as [])),
  withdrawSkill: (...a: unknown[]) => withdrawSkill(...(a as [])),
}));

function at(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/market/skills/*" element={<SkillsHubPage />} />
      </Routes>
    </MemoryRouter>,
  );
}
const me = (email: string, role: "employee" | "manager" | "admin" = "employee") =>
  useAuthStore.getState().setMe({ email, department: "研发部", role, managed_departments: role === "manager" ? ["研发部"] : [],
    auth_method: "password", must_change_password: false });

beforeEach(() => { reviewSkill.mockClear(); withdrawSkill.mockClear(); });
afterEach(cleanup);

describe("技能市场 · 审核", () => {
  it("列表上每条带状态; 老的 (legacy) 算已上架; 员工没有「待我审核」", async () => {
    me("alice@example.com");
    at("/market/skills");
    const card = (await screen.findByText("old-skill")).closest("a")!;
    expect(within(card).getByText("已上架")).toBeTruthy();
    const bad = screen.getByText("bad-skill").closest("a")!;
    expect(within(bad).getByText("被驳回")).toBeTruthy();
    expect(screen.queryByText(/待我审核/)).toBeNull();
    // 我提交的: 状态 + 撤回按钮
    expect(await screen.findByText("📤 我提交的")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "撤回" }));
    await waitFor(() => expect(withdrawSkill).toHaveBeenCalledWith("dept/研发部", "release-notes", "0.2.0"));
  });

  it("主管看到「待我审核」, 弹窗里有 SKILL.md 全文、文件、试用结果; 驳回必须写原因; 通过调 reviewSkill", async () => {
    me("boss@example.com", "manager");
    at("/market/skills");
    expect(await screen.findByText(/待我审核 \(1\)/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "审核" }));
    expect(await screen.findByText(/会打开 gitlab 看 MR/)).toBeTruthy();
    expect(screen.getByText("script.py")).toBeTruthy();
    expect(screen.getByText("提交说明：自动整理发版说明")).toBeTruthy();
    expect(screen.getByText("试跑成功")).toBeTruthy();
    expect(screen.getByText("生成说明")).toBeTruthy();
    expect(screen.getByText(/装到我这里试用/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "驳回" }));
    expect(await screen.findByText(/驳回要写原因/)).toBeTruthy();
    expect(reviewSkill).not.toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText(/审核意见/), { target: { value: "看过, 没问题" } });
    fireEvent.click(screen.getByRole("button", { name: "通过并上架" }));
    await waitFor(() => expect(reviewSkill).toHaveBeenCalledWith("dept/研发部", "release-notes", "0.2.0", "approve", "看过, 没问题"));
  });

  it("发布表单有范围和说明, 提交后说要等审核", async () => {
    me("alice@example.com");
    at("/market/skills/publish");
    expect(await screen.findByText("本部门")).toBeTruthy();
    expect(screen.getByText("全公司")).toBeTruthy();
    expect(screen.getByPlaceholderText(/这个技能做什么/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "提交审核" })).toBeTruthy();
  });
});
