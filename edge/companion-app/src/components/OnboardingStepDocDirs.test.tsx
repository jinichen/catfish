// @vitest-environment jsdom
/** 初始化「学你的文风」那步 (10/18 鸿波: 输入存放目录会出错, 除非是缺省目录): 选文件夹直接存; 手输照传; 出错要显示。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// 记录调用 + 换实现用普通函数, 不用 vi.fn 的 mockImplementation: vi.fn 会跟踪它返回的 promise,
// 配上 beforeEach 里的 mockReset, 被组件接住的拒绝也会被 vitest 算成测试失败 (单跑这条又是绿的)。
let impl: (cmd: string) => Promise<unknown> = async () => null;
const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({
  invoke: (cmd: string, args?: unknown) => { invoke(cmd, args); return impl(cmd); },
}));

import { StepDocDirs } from "./OnboardingStepsSetup";

beforeEach(() => { invoke.mockReset(); impl = async () => null; });
afterEach(cleanup);

describe("StepDocDirs", () => {
  it("选文件夹 → 直接加进搜索范围, 进下一步", async () => {
    impl = async (cmd) => (cmd === "local_search_pick_dir" ? "/Users/x/Documents/资料"
      : cmd === "local_search_scope_add" ? { include: [], exclude: [], yamlPath: "", added: "~/Documents/资料" } : { ok: true });
    const onNext = vi.fn();
    render(<StepDocDirs onNext={onNext} onBack={() => {}} onSkip={() => {}} />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /选择文件夹/ })); });
    expect(invoke).toHaveBeenCalledWith("local_search_scope_add", { path: "/Users/x/Documents/资料" });
    expect(invoke).toHaveBeenCalledWith("local_search_index", { only: "~/Documents/资料" });
    expect(onNext).toHaveBeenCalled();
  });

  it("选文件夹点了取消 → 什么都不做", async () => {
    impl = async () => null;
    const onNext = vi.fn();
    render(<StepDocDirs onNext={onNext} onBack={() => {}} onSkip={() => {}} />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /选择文件夹/ })); });
    expect(invoke).toHaveBeenCalledTimes(1);
    expect(onNext).not.toHaveBeenCalled();
  });

  it("手输的路径原样交给后端 (规整在后端做), 后端报错显示出来、不进下一步", async () => {
    // Tauri 命令出错时拒绝值是字符串 (Rust 的 Err(String)), 照真的来
    impl = () => Promise.reject("鲶鱼还没有权限读这个文件夹: /Users/x/Desktop/项目");
    const onNext = vi.fn();
    render(<StepDocDirs onNext={onNext} onBack={() => {}} onSkip={() => {}} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "~/桌面/项目" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /保存/ })); });
    expect(invoke).toHaveBeenCalledWith("local_search_scope_add", { path: "~/桌面/项目" });
    expect(screen.getByText(/还没有权限/)).toBeTruthy();
    expect(invoke).not.toHaveBeenCalledWith("local_search_index", expect.anything());
    expect(onNext).not.toHaveBeenCalled();
  });
});
