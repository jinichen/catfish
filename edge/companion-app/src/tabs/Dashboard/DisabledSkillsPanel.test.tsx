// @vitest-environment jsdom
/** 10/22: 默认停用的 hermes 自带技能 —— 列出来、能单个启用、能全部启用, 启用后刷新列表。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let disabled = ["arxiv", "github-auth"];
const setEnabled = vi.fn(async (names: string[]) => {
  const before = disabled.length;
  disabled = names.length ? disabled.filter((n) => !names.includes(n)) : [];
  return before - disabled.length;
});
vi.mock("../../lib/tauri", () => ({
  fetchDisabledSkills: () => Promise.resolve([...disabled]),
  setSkillsEnabled: (names: string[], enabled: boolean) => setEnabled(names, enabled),
}));

import DisabledSkillsPanel from "./DisabledSkillsPanel";

beforeEach(() => { disabled = ["arxiv", "github-auth"]; setEnabled.mockClear(); });
afterEach(cleanup);

describe("已停用的技能", () => {
  it("单个启用后从列表消失并提示新对话生效", async () => {
    render(<DisabledSkillsPanel />);
    expect(await screen.findByText("已停用的技能 (2)")).toBeTruthy();
    const btns = screen.getAllByText("启用");
    await act(async () => { fireEvent.click(btns[0]); });
    expect(setEnabled).toHaveBeenCalledWith(["arxiv"], true);
    expect(await screen.findByText("已停用的技能 (1)")).toBeTruthy();
    expect(screen.getByText(/新开的对话里生效/)).toBeTruthy();
  });

  it("全部启用传空列表", async () => {
    render(<DisabledSkillsPanel />);
    await screen.findByText("已停用的技能 (2)");
    await act(async () => { fireEvent.click(screen.getByText("全部启用")); });
    expect(setEnabled).toHaveBeenCalledWith([], true);
    expect(await screen.findByText("已停用的技能 (0)")).toBeTruthy();
  });
});
