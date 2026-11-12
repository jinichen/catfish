// @vitest-environment jsdom
/** 11/12 资料库「同事分享」分页: 部门知识库从 设置 → 技能 挪过来 (设置里不再有入口)。 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./WikiOrganizer", async () => {
  const React = await import("react");
  return {
    default: ({ mode, onModeChange, onTaskCount }: { mode: string; onModeChange: (m: string) => void; onTaskCount?: (n: number) => void }) =>
      React.createElement("div", null,
        React.createElement("span", { "data-testid": "mode" }, mode),
        React.createElement("button", { onClick: () => onModeChange("shared") }, "同事分享"),
        React.createElement("button", { onClick: () => onModeChange("organize") }, "关系整理"),
        React.createElement("button", { onClick: () => onTaskCount?.(0) }, "报0条"),
        React.createElement("button", { onClick: () => onTaskCount?.(3) }, "报3条")),
  };
});
vi.mock("./WikiPreview", () => ({ default: () => <div>正文</div> }));
vi.mock("./WikiGraph", () => ({ default: () => <div>图谱</div> }));
vi.mock("./WikiRelationshipWorkbench", () => ({ default: () => <div>关系整理台</div> }));
vi.mock("../Dashboard/WikiHubCard", () => ({ default: () => <div>部门知识库卡片</div> }));
vi.mock("../../store/wiki", () => ({ useWikiStore: (sel: (s: { refreshQuiet: () => void }) => unknown) => sel({ refreshQuiet: () => undefined }) }));

import WikiTab from "./WikiTab";

afterEach(cleanup);

describe("资料库 · 同事分享", () => {
  it("点「同事分享」: 中间换成部门知识库, 不出关联图谱按钮", () => {
    render(<WikiTab />);
    expect(screen.getByText("关系整理台")).toBeTruthy();
    fireEvent.click(screen.getByText("同事分享"));
    expect(screen.getByText("部门知识库卡片")).toBeTruthy();
    expect(screen.queryByText("查看关联图谱")).toBeNull();
  });

  it("11/12: 没有待整理的关系 → 一进来就开「全部知识」; 员工自己点回「关系整理」后不再自动换", () => {
    render(<WikiTab />);
    expect(screen.getByTestId("mode").textContent).toBe("organize");
    fireEvent.click(screen.getByText("报0条"));
    expect(screen.getByTestId("mode").textContent).toBe("browse");
    fireEvent.click(screen.getByText("关系整理"));
    fireEvent.click(screen.getByText("报0条"));
    expect(screen.getByTestId("mode").textContent).toBe("organize");
  });

  it("有待整理的关系 → 留在「关系整理」", () => {
    render(<WikiTab />);
    fireEvent.click(screen.getByText("报3条"));
    expect(screen.getByTestId("mode").textContent).toBe("organize");
  });
});
