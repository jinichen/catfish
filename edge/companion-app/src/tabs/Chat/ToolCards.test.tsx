// @vitest-environment jsdom
/** 11/15 对话卡片: 「出卡片」那次工具调用 → 按编号读出来画; 过期的说过期; 别的工具调用不画。 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import meeting from "./__fixtures__/a2ui_card_meeting.json";

const invoke = vi.fn();
let impl: ((...a: unknown[]) => unknown) | null = null;
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => (impl ? impl(...a) : invoke(...a)) }));

import ToolCards from "./ToolCards";
import { A2uiCardContext } from "../../lib/a2uiCardContext";

const ID = "c_20261115_093012_a1b2c3";
const call = (over: Record<string, unknown> = {}) => ({
  id: "t1", name: "mcp__catfish_tools__catfish_form", args: { action: "card", request: "选时间" }, status: "done" as const,
  result: JSON.stringify({ ok: true, card_id: ID, note: "…" }), ...over,
});

beforeEach(() => { invoke.mockReset(); impl = null; });
afterEach(cleanup);

describe("ToolCards", () => {
  it("按编号读出来画 (按需加载); 在对话页里能交, 交过的显示已交", { timeout: 20_000 }, async () => {
    invoke.mockResolvedValue(meeting);
    const { container } = render(
      <A2uiCardContext.Provider value={{ interactive: true, submitted: new Set([ID]) }}>
        <ToolCards calls={[call()]} />
      </A2uiCardContext.Provider>,
    );
    // 卡片组件按需加载, 第一次要转译 A2UI + Lit: 整套测试一起跑时默认 1 秒不够
    await waitFor(() => expect(container.querySelector(".a2ui-card")).toBeTruthy(), { timeout: 15_000 });
    expect(invoke).toHaveBeenCalledWith("a2ui_card_read", { cardId: ID });
    expect(screen.getByText("已交给小鲶 ✓")).toBeTruthy();
  });

  it("读不到 (过期清掉了) → 说原因, 不留空白", async () => {
    // vi.fn 记录返回值时会另挂一个 then, 被拒的 promise 在那条链上算「没人接」 —— 用个普通函数
    let calls = 0;
    impl = () => { calls++; return Promise.reject(new Error("卡片已过期 (本机只留 90 天) 或不在这台电脑上")); };
    render(<ToolCards calls={[call()]} />);
    await waitFor(() => expect(screen.getByRole("note").textContent).toContain("卡片已过期"));
    expect(calls).toBe(1);
  });

  it("别的工具调用 / 别的动作 / 没成功的 → 什么都不画, 也不去读", () => {
    const { container } = render(<ToolCards calls={[call({ name: "catfish_wiki_search" }), call({ args: { action: "create" } }),
      call({ result: JSON.stringify({ ok: false, error: "x" }) })]} />);
    expect(container.textContent).toBe("");
    expect(invoke).not.toHaveBeenCalled();
  });
});
