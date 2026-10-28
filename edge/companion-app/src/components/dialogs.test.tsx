// @vitest-environment jsdom
// 10/28 鸿波: 保留期改 7 天点保存没生效 —— Tauri WebView 里 window.confirm 不弹、直接当「取消」。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { alertDialog, confirmDialog, DialogHost, promptDialog } from "./dialogs";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("dialogs", () => {
  it("确认 → true, 取消 → false, 不碰 window.confirm", async () => {
    const native = vi.spyOn(window, "confirm");
    render(<DialogHost />);
    let p!: Promise<boolean>;
    act(() => { p = confirmDialog("改成 7 天会删掉旧记录, 确定吗?", "缩短保留期"); });
    expect(screen.getByText("缩短保留期")).toBeTruthy();
    fireEvent.click(screen.getByText("确认"));
    await expect(p).resolves.toBe(true);
    act(() => { p = confirmDialog("再来一次"); });
    fireEvent.click(screen.getByText("取消"));
    await expect(p).resolves.toBe(false);
    expect(native).not.toHaveBeenCalled();
    expect(screen.queryByText("再来一次")).toBeNull();
  });

  it("prompt 返回填的内容, 取消返回 null; alert 只有「知道了」", async () => {
    render(<DialogHost />);
    let p!: Promise<string | null>;
    act(() => { p = promptDialog("装到哪个 namespace?", "external"); });
    const input = screen.getByDisplayValue("external");
    fireEvent.change(input, { target: { value: "mine" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await expect(p).resolves.toBe("mine");
    act(() => { p = promptDialog("再填一次"); });
    fireEvent.click(screen.getByText("取消"));
    await expect(p).resolves.toBeNull();
    let a!: Promise<void>;
    act(() => { a = alertDialog("打开附件失败"); });
    expect(screen.queryByText("取消")).toBeNull();
    fireEvent.click(screen.getByText("知道了"));
    await expect(a).resolves.toBeUndefined();
  });

  it("同时来两个: 排队, 一个一个问", async () => {
    render(<DialogHost />);
    let p1!: Promise<boolean>, p2!: Promise<boolean>;
    act(() => { p1 = confirmDialog("第一个"); p2 = confirmDialog("第二个"); });
    expect(screen.getByText("第一个")).toBeTruthy();
    expect(screen.queryByText("第二个")).toBeNull();
    fireEvent.click(screen.getByText("确认"));
    expect(screen.getByText("第二个")).toBeTruthy();
    fireEvent.click(screen.getByText("取消"));
    await expect(p1).resolves.toBe(true);
    await expect(p2).resolves.toBe(false);
  });

  it("没挂 DialogHost (单测等) 退回原生", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await expect(confirmDialog("x")).resolves.toBe(true);
  });
});
