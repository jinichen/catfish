// @vitest-environment jsdom
/** 11/08 「我的技能」卡上分享: 直接选范围 (本部门 / 全公司) + 给审批人的说明, 不用再去对话里说。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tauri = vi.hoisted(() => ({ toolBridgeCallTool: vi.fn() }));
vi.mock("../../lib/tauri", () => tauri);
vi.mock("../../lib/tauri_skill_names", () => ({ backfillNamesOnce: vi.fn().mockResolvedValue(false), skillNameSet: () => new Set() }));
vi.mock("./SkillTrialUi", () => ({ TrialSkillsList: () => null }));
vi.mock("../../hooks/useIdentity", () => ({
  useMySkills: () => ({
    skills: [{ namespace: "personal", skills: [{ name: "eis-login", description: "登录 EIS", version: "1.0.0", path: "/tmp/s/eis-login", isProtected: false }] }],
    error: null, reload: vi.fn(),
  }),
}));

import MySkillsCard from "./MySkillsCard";

afterEach(cleanup);
beforeEach(() => tauri.toolBridgeCallTool.mockReset().mockResolvedValue({ ok: true, result: { ok: true, summary: "已提交, 等管理员审核" } }));

describe("MySkillsCard 分享范围", () => {
  it("默认本部门; 选全公司 + 写说明, 一起传给 catfish_skill_publish", async () => {
    render(<MySkillsCard />);
    fireEvent.click(screen.getByText(/分享/));
    expect((screen.getByLabelText(/本部门/) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByLabelText(/全公司/));
    fireEvent.change(screen.getByLabelText("给审批人的说明"), { target: { value: "EIS 下载证书用" } });
    fireEvent.click(screen.getByText("确认共享"));
    await waitFor(() => expect(tauri.toolBridgeCallTool).toHaveBeenCalled());
    expect(tauri.toolBridgeCallTool).toHaveBeenCalledWith("catfish_skill_publish",
      { skill_path: "/tmp/s/eis-login", namespace: "personal", scope: "company", note: "EIS 下载证书用" });
  });

  it("不改就是本部门, 说明空着不传", async () => {
    render(<MySkillsCard />);
    fireEvent.click(screen.getByText(/分享/));
    fireEvent.click(screen.getByText("确认共享"));
    await waitFor(() => expect(tauri.toolBridgeCallTool).toHaveBeenCalledWith("catfish_skill_publish",
      { skill_path: "/tmp/s/eis-login", namespace: "personal", scope: "department" }));
  });
});
