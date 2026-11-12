// @vitest-environment jsdom
/** 11/12 资料库「同事分享」分页: 部门知识库从 设置 → 技能 挪过来; 设置里的老入口一点就跳到这页。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./WikiOrganizer", async () => {
  const React = await import("react");
  return {
    default: ({ mode, onModeChange }: { mode: string; onModeChange: (m: string) => void }) =>
      React.createElement("div", null,
        React.createElement("span", { "data-testid": "mode" }, mode),
        React.createElement("button", { onClick: () => onModeChange("shared") }, "同事分享")),
  };
});
vi.mock("./WikiPreview", () => ({ default: () => <div>正文</div> }));
vi.mock("./WikiGraph", () => ({ default: () => <div>图谱</div> }));
vi.mock("./WikiRelationshipWorkbench", () => ({ default: () => <div>关系整理台</div> }));
vi.mock("../Dashboard/WikiHubCard", () => ({ default: () => <div>部门知识库卡片</div> }));
vi.mock("../../store/wiki", () => ({ useWikiStore: (sel: (s: { refreshQuiet: () => void }) => unknown) => sel({ refreshQuiet: () => undefined }) }));

import { useUIStore } from "../../store/ui";
import WikiHubMovedCard from "../Dashboard/WikiHubMovedCard";
import WikiTab from "./WikiTab";

afterEach(cleanup);
beforeEach(() => useUIStore.setState({ activeTab: "dashboard", pendingWikiShared: false }));

describe("资料库 · 同事分享", () => {
  it("点「同事分享」: 中间换成部门知识库, 不出关联图谱按钮", () => {
    render(<WikiTab />);
    expect(screen.getByText("关系整理台")).toBeTruthy();
    fireEvent.click(screen.getByText("同事分享"));
    expect(screen.getByText("部门知识库卡片")).toBeTruthy();
    expect(screen.queryByText("查看关联图谱")).toBeNull();
  });

  it("设置里的老入口: 点「去资料库看」→ 切到资料库并打开同事分享 (只用一次)", () => {
    render(<WikiHubMovedCard />);
    fireEvent.click(screen.getByText("去资料库看"));
    expect(useUIStore.getState().activeTab).toBe("wiki");
    cleanup();
    render(<WikiTab />);
    expect(screen.getByTestId("mode").textContent).toBe("shared");
    expect(useUIStore.getState().pendingWikiShared).toBe(false);
  });

  it("资料库已经开着时再点入口, 也会切过去", () => {
    render(<WikiTab />);
    expect(screen.getByTestId("mode").textContent).toBe("organize");
    act(() => useUIStore.getState().openWikiShared());
    expect(screen.getByTestId("mode").textContent).toBe("shared");
  });
});
