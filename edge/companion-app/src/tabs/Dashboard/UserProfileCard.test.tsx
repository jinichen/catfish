// @vitest-environment jsdom
/** 11/12 画像页: 只显示当前值 + 待你确认的提议 (带你说过的原话); 自己改直接生效; 「不用」只撤提议。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tauri = vi.hoisted(() => ({ toolBridgeCallTool: vi.fn() }));
vi.mock("../../lib/tauri", () => tauri);
vi.mock("../../store/agent", () => ({ useAgentStore: (sel: (s: { name: string }) => unknown) => sel({ name: "小鲶" }) }));

import UserProfileCard from "./UserProfileCard";

const field = (value: string | null, extra: Record<string, unknown> = {}) => ({
  value, options: ["列表", "段落", "混合"], locked: false, last_confirmed: null, proposed_value: null, proposed_quote: null, ...extra,
});
const PROFILE = {
  "writing_style.bullet_pref": field("列表", { proposed_value: "段落", proposed_quote: "汇报别用列表了" }),
  "writing_style.tone": { ...field("casual"), options: ["formal", "casual", "直接", "委婉", "幽默"] },
  "work_pattern.peak_hours": { ...field(null), options: null },
};

afterEach(cleanup);
beforeEach(() => tauri.toolBridgeCallTool.mockReset().mockImplementation(async (name: string) =>
  name === "catfish_user_profile_get"
    ? { ok: true, result: { type: "result", result: PROFILE } }
    : { ok: true, result: { type: "result", result: {} } }));

describe("UserProfileCard", () => {
  it("显示当前值和待确认的提议 (带原话), 不显示 evidence 次数", async () => {
    render(<UserProfileCard />);
    await screen.findByText(/想改成「段落」/);
    expect(screen.getByText(/汇报别用列表了/)).toBeTruthy();
    expect(screen.getByText("1 项等你确认")).toBeTruthy();
    expect((screen.getByLabelText("语气") as HTMLSelectElement).value).toBe("casual");
    expect(screen.getByRole("option", { name: "随意" })).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/evidence|正在观察/);
  });

  it("确认 = confirm 提议的值; 不用 = 只撤提议", async () => {
    render(<UserProfileCard />);
    fireEvent.click(await screen.findByText("确认"));
    await waitFor(() => expect(tauri.toolBridgeCallTool).toHaveBeenCalledWith("catfish_user_profile_confirm",
      { field: "writing_style.bullet_pref", value: "段落", locked: false }));
    fireEvent.click(await screen.findByText("不用"));
    await waitFor(() => expect(tauri.toolBridgeCallTool).toHaveBeenCalledWith("catfish_user_profile_clear",
      { field: "writing_style.bullet_pref", proposal_only: true }));
  });

  it("自己改直接生效, 改成「还没定」就清掉这一项", async () => {
    render(<UserProfileCard />);
    const sel = await screen.findByLabelText("语气");
    fireEvent.change(sel, { target: { value: "直接" } });
    await waitFor(() => expect(tauri.toolBridgeCallTool).toHaveBeenCalledWith("catfish_user_profile_confirm",
      { field: "writing_style.tone", value: "直接", locked: false }));
    fireEvent.change(sel, { target: { value: "" } });
    await waitFor(() => expect(tauri.toolBridgeCallTool).toHaveBeenCalledWith("catfish_user_profile_clear", { field: "writing_style.tone" }));
  });

  it("自由文本项: 回车保存", async () => {
    render(<UserProfileCard />);
    const inp = await screen.findByLabelText("工作黄金时段");
    fireEvent.change(inp, { target: { value: "9:00-12:00" } });
    fireEvent.blur(inp);
    await waitFor(() => expect(tauri.toolBridgeCallTool).toHaveBeenCalledWith("catfish_user_profile_confirm",
      { field: "work_pattern.peak_hours", value: "9:00-12:00", locked: false }));
  });
});
