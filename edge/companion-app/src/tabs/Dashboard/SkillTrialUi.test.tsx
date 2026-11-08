// @vitest-environment jsdom
/** 技能的审批人试用 (11/08): 待我审列表 + 「装到我这里试用」; 试用中列表的标 / 回传 / 卸掉; 自动卸掉后的一句话。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  trialSkillPending: vi.fn(),
  trialSkillInstall: vi.fn(),
  trialSkillList: vi.fn(),
  trialSkillUninstall: vi.fn(),
  trialSkillResult: vi.fn(),
  trialSkillSync: vi.fn(),
  trialRuns: vi.fn(),
}));
vi.mock("../../lib/tauri_trial", async (orig) => ({ ...(await orig<typeof import("../../lib/tauri_trial")>()), ...api }));

import { PendingSkillsSection, TrialSkillsList, removedSkillText } from "./SkillTrialUi";

const PENDING = {
  namespace: "department", name: "eis-login", version: "1.0.0", scope: "department" as const, departments: ["安全部"],
  status: "pending" as const, submitted_by: "alice@x", submitted_at: "2026-11-08T00:00:00Z", submit_note: "登录 EIS 用",
};
const TRIAL = {
  skill_path: "department/eis-login", path: "/x", title: "登录 EIS", summary: "", namespace: "department", name: "eis-login",
  version: "1.0.0", by: "boss@x", expires: Math.floor(Date.now() / 1000) + 5 * 86400, installed_at: "", expired: false,
};

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.trialRuns.mockResolvedValue({ runs: [{ run_id: "r_20261108000000_abcdef", skill_path: "department/eis-login", title: "登录 EIS",
                                              status: "done", created_at: "2026-11-08T01:00:00Z" }] });
});
afterEach(cleanup);

describe("PendingSkillsSection", () => {
  it("不是审批人 (空列表) 什么都不显示", async () => {
    api.trialSkillPending.mockResolvedValue({ skills: [] });
    const { container } = render(<PendingSkillsSection />);
    await act(async () => {});
    expect(container.textContent).toBe("");
  });

  it("待审的列出来, 点「装到我这里试用」走试用安装并把结果说出来", async () => {
    api.trialSkillPending.mockResolvedValue({ skills: [PENDING] });
    api.trialSkillInstall.mockResolvedValue({ ok: true, installed_path: "department/eis-login", summary: "已以试用身份装到你这台电脑" });
    const done = vi.fn();
    render(<PendingSkillsSection onInstalled={done} />);
    expect(await screen.findByText(/提交人说: 登录 EIS 用/)).toBeTruthy();
    expect(screen.getByText(/安全部 · alice@x 提交/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "装到我这里试用" })); });
    expect(api.trialSkillInstall).toHaveBeenCalledWith("department", "eis-login", "1.0.0");
    expect(done).toHaveBeenCalledWith("已以试用身份装到你这台电脑");
  });

  it("验签不过的拒装, 原话显示", async () => {
    api.trialSkillPending.mockResolvedValue({ skills: [PENDING] });
    api.trialSkillInstall.mockRejectedValue(new Error("下载到的文件跟中央记录的不一致 (指纹对不上), 不装"));
    render(<PendingSkillsSection />);
    const btn = await screen.findByRole("button", { name: "装到我这里试用" });
    await act(async () => { fireEvent.click(btn); });
    expect(screen.getByText(/指纹对不上/)).toBeTruthy();
  });
});

describe("TrialSkillsList", () => {
  it("试用中的带「试用」标; 回传结果带上挑的运行记录; 卸掉说清恢复了正式版", async () => {
    api.trialSkillList.mockResolvedValue({ trials: [TRIAL] });
    api.trialSkillResult.mockResolvedValue({ ok: true, summary: "试用结果已回传, 审核页上能看到" });
    api.trialSkillUninstall.mockResolvedValue({ ok: true, restored: "/x" });
    const changed = vi.fn();
    render(<TrialSkillsList onChanged={changed} />);
    expect(await screen.findByText("试用")).toBeTruthy();
    expect(screen.getByText("登录 EIS")).toBeTruthy();

    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "回传试用结果" })); });
    expect(await screen.findByText(/附上哪次运行/)).toBeTruthy();
    fireEvent.click(screen.getByLabelText("不行"));
    fireEvent.change(screen.getByPlaceholderText(/一句话结论/), { target: { value: "导出少一列" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "回传" })); });
    expect(api.trialSkillResult).toHaveBeenCalledWith("department/eis-login", false, "导出少一列", "r_20261108000000_abcdef");
    expect(changed).toHaveBeenCalledWith("试用结果已回传, 审核页上能看到");

    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "卸掉试用" })); });
    expect(api.trialSkillUninstall).toHaveBeenCalledWith("department/eis-login");
    expect(changed.mock.calls.at(-1)?.[0]).toMatch(/已卸掉试用 登录 EIS \(试用前的正式版本已恢复\)/);
  });

  it("没选能用 / 不行就不让回传", async () => {
    api.trialSkillList.mockResolvedValue({ trials: [TRIAL] });
    render(<TrialSkillsList />);
    const open = await screen.findByRole("button", { name: "回传试用结果" });
    await act(async () => { fireEvent.click(open); });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "回传" })); });
    expect(screen.getByText(/先选「能用」还是「不行」/)).toBeTruthy();
    expect(api.trialSkillResult).not.toHaveBeenCalled();
  });
});

describe("removedSkillText", () => {
  it("通过的提示转正式, 驳回 / 到期的说明原因", () => {
    expect(removedSkillText({ ...TRIAL, status: "approved" })).toMatch(/审核通过了.*转成正式安装/);
    expect(removedSkillText({ ...TRIAL, status: "rejected" })).toMatch(/被驳回, 试用安装已自动卸掉/);
    expect(removedSkillText({ ...TRIAL, status: "expired" })).toMatch(/试用到期/);
  });
});
