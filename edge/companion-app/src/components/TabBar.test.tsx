// @vitest-environment jsdom
/** 左边栏 (10/3): 最后一项叫「设置」; 能收起成只剩图标, 收起时名字放 tooltip, 记住状态。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/roomLinkStore", () => ({ pendingCount: () => 0, useRoomLink: () => ({}) }));
vi.mock("../lib/taskDoneStore", () => ({ markSeen: vi.fn(), unseenCount: () => 0, useTaskDone: () => ({}) }));
vi.mock("../lib/runtime", () => ({ isTauriRuntime: () => false }));

import TabBar from "./TabBar";
import { useUIStore } from "../store/ui";

let stored: Map<string, string>;
beforeEach(() => {
  stored = new Map();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: { getItem: (k: string) => stored.get(k) ?? null, setItem: (k: string, v: string) => void stored.set(k, v) },
  });
  useUIStore.setState({ railCollapsed: false });
});
afterEach(cleanup);

describe("TabBar", () => {
  it("最后一项是「设置」, 不再叫仪表盘", () => {
    render(<TabBar />);
    const items = screen.getByRole("navigation").querySelectorAll(".app-rail__nav .app-rail__item");
    expect(items[items.length - 1].textContent).toBe("设置");
    expect(screen.queryByText("仪表盘")).toBeNull();
  });

  it("收起: 打标记、名字进 tooltip、记进 localStorage; 再点展开", () => {
    render(<TabBar />);
    const nav = screen.getByRole("navigation");
    expect(nav.getAttribute("data-collapsed")).toBeNull();
    expect(screen.getByRole("button", { name: "设置" }).getAttribute("title")).toBeNull();

    act(() => { fireEvent.click(screen.getByRole("button", { name: "收起侧栏" })); });
    expect(nav.getAttribute("data-collapsed")).toBe("true");
    expect(screen.getByRole("button", { name: "设置" }).getAttribute("title")).toBe("设置");
    expect(stored.get("catfish:railCollapsed")).toBe("1");

    act(() => { fireEvent.click(screen.getByRole("button", { name: "展开侧栏" })); });
    expect(nav.getAttribute("data-collapsed")).toBeNull();
    expect(stored.get("catfish:railCollapsed")).toBe("0");
  });
});
