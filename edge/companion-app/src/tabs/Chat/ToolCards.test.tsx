// @vitest-environment jsdom
/** 11/15 对话卡片: 「出卡片」那次工具调用 → 按编号读出来画; 过期的说过期; 别的工具调用不画。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import meeting from "./__fixtures__/a2ui_card_meeting.json";

const invoke = vi.fn();
let impl: ((...a: unknown[]) => unknown) | null = null;
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => (impl ? impl(...a) : invoke(...a)) }));

import ToolCards from "./ToolCards";
import CardsPane from "./artifacts/CardsPane";
import { A2uiCardContext } from "../../lib/a2uiCardContext";
import { useArtifactStore } from "../../store/artifacts";
import { cardTime, useCardPanelStore } from "../../store/cardPanel";

const ID = "c_20261115_093012_a1b2c3";
const call = (over: Record<string, unknown> = {}) => ({
  id: "t1", name: "mcp__catfish_tools__catfish_form", args: { action: "card", request: "选时间" }, status: "done" as const,
  result: JSON.stringify({ ok: true, card_id: ID, note: "…" }), ...over,
});

beforeEach(() => {
  invoke.mockReset(); impl = null;
  useCardPanelStore.getState().reset();
  useArtifactStore.setState({ view: null, userActedAt: 0 });
});
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

  it("card_update: 不再画一张, 留一行小字, 原来那张卡重读文件", { timeout: 20_000 }, async () => {
    invoke.mockResolvedValue(meeting);
    const first = render(<ToolCards calls={[call()]} />);
    await waitFor(() => expect(first.container.querySelector(".a2ui-card")).toBeTruthy(), { timeout: 15_000 });
    expect(invoke).toHaveBeenCalledTimes(1);
    const up = call({ id: "t2", args: { action: "card_update", card_id: ID, data: { "/f/agenda": "x" } },
      result: JSON.stringify({ ok: true, card_id: ID, revision: 1, note: "…" }) });
    const second = render(<ToolCards calls={[up]} />);
    expect(second.container.textContent).toContain("更新了上面的卡片");
    expect(second.container.querySelector(".a2ui-card")).toBeNull();
    await waitFor(() => expect(invoke).toHaveBeenCalledTimes(2));
    expect(invoke).toHaveBeenLastCalledWith("a2ui_card_read", { cardId: ID });
  });

  it("别的工具调用 / 别的动作 / 没成功的 → 什么都不画, 也不去读", () => {
    const { container } = render(<ToolCards calls={[call({ name: "catfish_wiki_search" }), call({ args: { action: "create" } }),
      call({ result: JSON.stringify({ ok: false, error: "x" }) })]} />);
    expect(container.textContent).toBe("");
    expect(invoke).not.toHaveBeenCalled();
  });

  it("对话页 (有右栏): 要填的卡放右栏「待处理」, 对话里只留一行; 新出的自动打开右栏", async () => {
    invoke.mockResolvedValue(meeting);
    const now = new Date();
    const pad = (n: number) => String(n).padStart(2, "0");
    const fresh = `c_${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}_abcdef`;
    const { container } = render(
      <A2uiCardContext.Provider value={{ interactive: true, submitted: new Set(), panel: true }}>
        <ToolCards calls={[call({ result: JSON.stringify({ ok: true, card_id: fresh }) })]} />
      </A2uiCardContext.Provider>,
    );
    await waitFor(() => expect(container.querySelector("[data-card-stub]")).toBeTruthy());
    expect(container.textContent).toContain("约周例会");
    expect(container.textContent).toContain("待你确认");
    expect(container.querySelector(".a2ui-card")).toBeNull();
    expect(useCardPanelStore.getState().cards.map((c) => [c.cardId, c.title, c.submitted])).toEqual([[fresh, "约周例会", false]]);
    expect(useArtifactStore.getState().view).toBe("cards");
    // 点「在右边看」: 切到待处理、记下要看哪张
    useArtifactStore.setState({ view: null });
    fireEvent.click(screen.getByText("在右边看 →"));
    expect(useArtifactStore.getState().view).toBe("cards");
    expect(useCardPanelStore.getState().focus?.cardId).toBe(fresh);
  });

  it("老会话里的卡 (不是这一轮的): 照样放右栏, 但不自动弹开; 交过的写「已交给小鲶」", async () => {
    invoke.mockResolvedValue(meeting);
    const { container } = render(
      <A2uiCardContext.Provider value={{ interactive: true, submitted: new Set([ID]), panel: true }}>
        <ToolCards calls={[call()]} />
      </A2uiCardContext.Provider>,
    );
    await waitFor(() => expect(container.querySelector("[data-card-stub]")).toBeTruthy());
    expect(container.textContent).toContain("已交给小鲶 ✓");
    expect(useArtifactStore.getState().view).toBeNull();
    expect(cardTime(ID)).toBe(new Date(2026, 10, 15, 9, 30, 12).getTime());
  });

  it("小鲶改了右栏里的卡: 对话里写「更新了右边的卡片」, 点「看一眼」切过去", async () => {
    useCardPanelStore.getState().register({ cardId: ID, title: "约周例会", submitted: false, interactive: true, at: cardTime(ID) });
    const up = call({ id: "t2", args: { action: "card_update", card_id: ID, data: {} }, result: JSON.stringify({ ok: true, card_id: ID, revision: 2 }) });
    const { container } = render(<ToolCards calls={[up]} />);
    expect(container.textContent).toContain("更新了右边的卡片「约周例会」");
    fireEvent.click(screen.getByText("看一眼"));
    expect(useArtifactStore.getState().view).toBe("cards");
  });

  it("右栏「待处理」: 待确认的画出来; 交过的收在「已处理」, 点开才画", { timeout: 20_000 }, async () => {
    invoke.mockResolvedValue(meeting);
    const s = useCardPanelStore.getState();
    s.register({ cardId: ID, title: "约周例会", submitted: false, interactive: true, at: cardTime(ID) });
    s.register({ cardId: "c_20261114_080000_000001", title: "旧卡", submitted: true, interactive: true, at: cardTime("c_20261114_080000_000001") });
    const { container } = render(<CardsPane />);
    await waitFor(() => expect(container.querySelectorAll(".a2ui-card").length).toBe(1), { timeout: 15_000 });
    expect(container.textContent).toContain("待你确认");
    fireEvent.click(screen.getByText(/已处理 \(1\)/));
    await waitFor(() => expect(container.querySelectorAll(".a2ui-card").length).toBe(2), { timeout: 15_000 });
  });
});
