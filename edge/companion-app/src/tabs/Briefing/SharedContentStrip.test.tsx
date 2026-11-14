// @vitest-environment jsdom
/** 11/14: 早安「你装的共享内容」—— 有新版 / 被撤回 / 看不到了才显示; 更新走原来的安装工具; 「知道了」收起这一批 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a) }));

import { installMemoryLocalStorage } from "../../lib/__fixtures__/memoryLocalStorage";
import SharedContentStrip from "./SharedContentStrip";
import { useUIStore } from "../../store/ui";

const SKILL_UPD = { kind: "skill", key: "department/eis-login", title: "eis-login", status: "approved", namespace: "department",
  name: "eis-login", installed_version: "1.0.0", latest_version: "1.10.0" };
const EXPERT_GONE = { kind: "expert", key: "qual", title: "资质专员", status: "withdrawn", id: "qual", installed_version: "1.0.0",
  latest_version: null, withdrawn_by: "boss@x", note: "流程改了" };
const WIKI_UPD = { kind: "wiki", key: "dept/研发部/abc", title: "外架验收经验", status: "update", namespace: "dept/研发部", file_id: "abc" };

function route(last: unknown, run: unknown = last, tool: unknown = { ok: true, result: { ok: true } }) {
  invoke.mockImplementation((cmd: string) => {
    if (cmd === "shared_check_last") return Promise.resolve(last);
    if (cmd === "shared_check_run") return Promise.resolve(run);
    if (cmd === "tool_bridge_call_tool") return Promise.resolve(tool);
    return Promise.reject(new Error(`unexpected ${cmd}`));
  });
}

installMemoryLocalStorage();

beforeEach(() => { invoke.mockReset(); localStorage.clear(); });
afterEach(cleanup);

describe("SharedContentStrip", () => {
  it("什么都没变不显示", async () => {
    route({ ok: true, updates: [], withdrawn: [], hidden: [] });
    const { container } = render(<SharedContentStrip />);
    await new Promise((r) => setTimeout(r, 0));
    expect(container.innerHTML).toBe("");
  });

  it("汇总一行; 展开后撤回的写清谁撤的、本机已停用", async () => {
    route({ updates: [SKILL_UPD, WIKI_UPD], withdrawn: [EXPERT_GONE], hidden: [] });
    render(<SharedContentStrip />);
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("2 个有新版 · 1 个已被撤回"));
    fireEvent.click(screen.getByText("看看"));
    const text = screen.getByRole("status").textContent ?? "";
    expect(text).toContain("v1.0.0 → v1.10.0");
    expect(text).toContain("已被撤回 (boss@x): 流程改了");
    expect(text).toContain("本机已停用");
    expect(text).toContain("作者改过了");
  });

  it("技能「更新」走 catfish_skill_install 覆盖装最新版, 装完重新查", async () => {
    route({ updates: [SKILL_UPD] }, { updates: [] });
    render(<SharedContentStrip />);
    await waitFor(() => screen.getByText("看看"));
    fireEvent.click(screen.getByText("看看"));
    fireEvent.click(screen.getByText("更新"));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("shared_check_run"));
    const call = invoke.mock.calls.find((c) => c[0] === "tool_bridge_call_tool");
    expect(call?.[1]).toMatchObject({ name: "catfish_skill_install",
      args: { hub_skill: "department/eis-login@1.10.0", overwrite: true } });
  });

  it("知识页「更新」走 catfish_wiki_install; 失败把原因说出来", async () => {
    route({ updates: [WIKI_UPD] }, { updates: [] }, { ok: true, result: { ok: false, error: "不是你的部门" } });
    render(<SharedContentStrip />);
    await waitFor(() => screen.getByText("看看"));
    fireEvent.click(screen.getByText("看看"));
    fireEvent.click(screen.getByText("更新"));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("外架验收经验 没更新成: 不是你的部门"));
    const call = invoke.mock.calls.find((c) => c[0] === "tool_bridge_call_tool");
    expect(call?.[1]).toMatchObject({ name: "catfish_wiki_install", args: { hub_namespace: "dept/研发部", hub_file_id: "abc" } });
  });

  it("专家包有新版: 去专家市场确认 (升级可能多要权限)", async () => {
    route({ updates: [{ ...EXPERT_GONE, status: "approved", latest_version: "1.2.0" }] });
    useUIStore.getState().setActiveTab("briefing");
    render(<SharedContentStrip />);
    await waitFor(() => screen.getByText("看看"));
    fireEvent.click(screen.getByText("看看"));
    fireEvent.click(screen.getByText("去专家市场"));
    expect(useUIStore.getState().activeTab).toBe("dashboard");
    expect(invoke.mock.calls.some((c) => c[0] === "tool_bridge_call_tool")).toBe(false);
  });

  it("「知道了」收起这一批; 有新变化再出来", async () => {
    route({ updates: [SKILL_UPD] });
    const { container, unmount } = render(<SharedContentStrip />);
    await waitFor(() => screen.getByText("知道了"));
    fireEvent.click(screen.getByText("知道了"));
    expect(container.innerHTML).toBe("");
    unmount();
    route({ updates: [SKILL_UPD], withdrawn: [EXPERT_GONE] });
    render(<SharedContentStrip />);
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("1 个已被撤回"));
  });
});
