// @vitest-environment jsdom
/** 11/14: 技能市场「一键升级」—— 安装工具自己返失败 (中央找不到这个版本) 时, 不能再弹「升级成功」 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a) }));
const fetchWithAuth = vi.fn();
vi.mock("../../lib/me", () => ({ fetchWithAuth: (...a: unknown[]) => fetchWithAuth(...a), getCurrentUserEmail: () => Promise.resolve("me@example.com") }));

import SkillsHubCard from "./SkillsHubCard";

const HUB = { skills: [{ namespace: "department", name: "eis-login", latest_version: "1.0.0", description: "登录 EIS",
  published_by: "zhou@example.com", published_at: new Date().toISOString(), review: { status: "approved" } }] };
const MINE = [{ namespace: "department", skills: [{ name: "eis-login", description: "", version: "0.1.0-frozen", path: "x",
  isProtected: false, title: "eis-login", summary: "" }] }];

function route(toolResult: unknown) {
  invoke.mockImplementation((cmd: string) => {
    if (cmd === "list_my_skills") return Promise.resolve(MINE);
    if (cmd === "share_trial_call") return Promise.resolve({ removed: [], skills: [] });
    if (cmd === "tool_bridge_call_tool") return Promise.resolve(toolResult);
    return Promise.resolve(null);
  });
}

beforeEach(() => {
  invoke.mockReset();
  fetchWithAuth.mockReset().mockImplementation(() => Promise.resolve(new Response(JSON.stringify(HUB), { status: 200 })));
});
afterEach(cleanup);

describe("SkillsHubCard 一键升级", () => {
  it("工具调到了但安装失败 → 提示升级失败和原因, 不说成功", async () => {
    route({ ok: true, tool: "catfish_skill_install", result: { ok: false, error: "hub 里找不到 department/eis-login@1.0.0" } });
    render(<SkillsHubCard />);
    await waitFor(() => screen.getByText("一键升级"));
    fireEvent.click(screen.getByText("一键升级"));
    await waitFor(() => expect(document.body.textContent).toContain("升级失败: hub 里找不到 department/eis-login@1.0.0"));
    expect(document.body.textContent).not.toContain("升级成功");
  });

  it("真装上了 → 升级成功", async () => {
    route({ ok: true, tool: "catfish_skill_install", result: { ok: true, installed_path: "department/eis-login" } });
    render(<SkillsHubCard />);
    await waitFor(() => screen.getByText("一键升级"));
    fireEvent.click(screen.getByText("一键升级"));
    await waitFor(() => expect(document.body.textContent).toContain("升级成功: department/eis-login → v1.0.0"));
  });
});

describe("SkillsHubCard 自己分享的", () => {
  it("不当成同事分享列 (不会出现「已装」「一键升级」), 底下说一句审核到哪了", async () => {
    route({ ok: true, result: { ok: true } });
    fetchWithAuth.mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ skills: [
      ...HUB.skills,
      { namespace: "personal", name: "video-audio-summary", latest_version: "1.0.0", description: "转写摘要",
        published_by: "me@example.com", published_at: new Date().toISOString(), review: { status: "approved" } }] }), { status: 200 })));
    render(<SkillsHubCard />);
    await waitFor(() => expect(document.body.textContent).toContain("你自己分享的 1 个不在上面列"));
    const text = document.body.textContent ?? "";
    expect(text).toContain("video-audio-summary v1.0.0 已上架");
    expect(text).toContain("(1 skill / 1 分组)");
    expect(screen.getAllByText("一键升级")).toHaveLength(1);
  });
});

