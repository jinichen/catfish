// @vitest-environment jsdom
/** 11/07: 正文里的文件名 —— 对话页里能预览的在右侧成果区打开; 别的页面 / 预览不了的照旧用默认程序打开 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a), convertFileSrc: (p: string) => p }));

import { Markdown } from "./markdown";
import FilePill from "../components/FilePill";
import { useArtifactStore } from "../store/artifacts";

beforeEach(() => {
  invoke.mockReset();
  invoke.mockResolvedValue(undefined);
  useArtifactStore.setState({ path: null });
});
afterEach(cleanup);

describe("正文文件名点击", () => {
  it("对话页里的网页 → 右侧成果区", () => {
    render(<div className="chat-workspace"><Markdown text="做好了 /Users/x/out/chart.html 请看" /></div>);
    fireEvent.click(screen.getByRole("button", { name: /chart\.html/ }));
    expect(useArtifactStore.getState().path).toBe("/Users/x/out/chart.html");
    expect(invoke).not.toHaveBeenCalled();
  });

  it("早安简报这类页面 (没有成果区) → 照旧用默认程序打开", () => {
    render(<div><Markdown text="做好了 /Users/x/out/chart.html 请看" /></div>);
    fireEvent.click(screen.getByRole("button", { name: /chart\.html/ }));
    expect(useArtifactStore.getState().path).toBeNull();
    expect(invoke).toHaveBeenCalledWith("open_file", { path: "/Users/x/out/chart.html" });
  });

  it("压缩包预览不了 → 默认程序", () => {
    render(<div className="chat-workspace"><Markdown text="打包在 /Users/x/out/all.zip" /></div>);
    fireEvent.click(screen.getByRole("button", { name: /all\.zip/ }));
    expect(useArtifactStore.getState().path).toBeNull();
    expect(invoke).toHaveBeenCalledWith("open_file", { path: "/Users/x/out/all.zip" });
  });
});

describe("文件胶囊", () => {
  it("能预览的有「预览」, 点了在右侧打开", () => {
    render(<FilePill path="/Users/x/out/周报.docx" />);
    fireEvent.click(screen.getByRole("button", { name: "预览" }));
    expect(useArtifactStore.getState().path).toBe("/Users/x/out/周报.docx");
  });

  it("预览不了的没有「预览」", () => {
    render(<FilePill path="/Users/x/out/all.zip" />);
    expect(screen.queryByRole("button", { name: "预览" })).toBeNull();
  });
});
