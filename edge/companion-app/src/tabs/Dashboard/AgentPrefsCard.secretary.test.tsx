// @vitest-environment jsdom
/** 11/09 仪表盘「小鲶秘书」开关按登记列 (lib/secretary.ts); 关掉时说一句撤了什么。 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/app", () => ({ getVersion: async () => "1.2.133" }));
vi.mock("../../lib/tauri", async (orig) => ({ ...(await orig<typeof import("../../lib/tauri")>()), emailConfigGet: vi.fn(async () => null) }));
const setAbilityEnabled = vi.hoisted(() => vi.fn());
vi.mock("../../lib/secretary", async (orig) => ({ ...(await orig<typeof import("../../lib/secretary")>()), setAbilityEnabled }));

import { installMemoryLocalStorage } from "../../lib/__fixtures__/memoryLocalStorage";
import AgentPrefsCard from "./AgentPrefsCard";

installMemoryLocalStorage();
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("AgentPrefsCard 小鲶秘书开关", () => {
  it("按登记列出三个有开关的能力 (做完就报不列); 关掉「提前准备」说一句撤了几条", async () => {
    setAbilityEnabled.mockResolvedValue("已经排上、还没做的 2 条准备任务撤了");
    render(<AgentPrefsCard />);
    expect(screen.getByText(/发现周期性的事，问要不要托管/)).toBeTruthy();
    expect(screen.getByText(/群里 @我 交代的事挂到提醒条/)).toBeTruthy();
    expect(screen.queryByText(/定时任务做完就报/)).toBeNull();
    const row = screen.getByText(/📋 提前准备/).closest("div")!;
    fireEvent.click(Array.from(row.querySelectorAll("button")).find((b) => b.textContent === "关")!);
    expect(setAbilityEnabled).toHaveBeenCalledWith(expect.objectContaining({ id: "advance-prep" }), false);
    expect(await screen.findByText("已关「提前准备」: 已经排上、还没做的 2 条准备任务撤了")).toBeTruthy();
  });
});
