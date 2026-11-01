// @vitest-environment jsdom
/** 手机配对确认 (11/01): 显示验证码, 员工点允许 / 拒绝才回中转站; 超上限时说清楚。 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const handlers: Record<string, (e: { payload: unknown }) => void> = {};
vi.mock("@tauri-apps/api/event", () => ({
  listen: (name: string, cb: (e: { payload: unknown }) => void) => {
    handlers[name] = cb;
    return Promise.resolve(() => undefined);
  },
}));
const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a) }));

import MobilePairDialog from "./MobilePairDialog";

beforeEach(() => {
  invoke.mockReset().mockImplementation((cmd: string) =>
    Promise.resolve(cmd === "mobile_status" ? { pending: [] } : undefined));
});
afterEach(cleanup);

describe("MobilePairDialog", () => {
  it("来了请求显示验证码, 点允许才回", async () => {
    render(<MobilePairDialog />);
    await waitFor(() => expect(handlers["mobile://pair-request"]).toBeTruthy());
    act(() => handlers["mobile://pair-request"]({ payload: { device: "phone-1", name: "iPhone", code: "025159" } }));
    expect(screen.getByLabelText("验证码").textContent).toBe("025159");
    expect(invoke).not.toHaveBeenCalledWith("mobile_pair_decide", expect.anything());
    fireEvent.click(screen.getByText("数字一样, 允许"));
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith("mobile_pair_decide", { device: "phone-1", allow: true, replace: null }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("拒绝也回中转站", async () => {
    render(<MobilePairDialog />);
    await waitFor(() => expect(handlers["mobile://pair-request"]).toBeTruthy());
    act(() => handlers["mobile://pair-request"]({ payload: { device: "phone-2", name: "安卓", code: "111111" } }));
    fireEvent.click(screen.getByText("拒绝"));
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith("mobile_pair_decide", { device: "phone-2", allow: false, replace: null }));
  });

  it("打开窗口前就来了的请求也补上", async () => {
    invoke.mockImplementation((cmd: string) => Promise.resolve(
      cmd === "mobile_status" ? { pending: [{ device: "phone-3", name: "iPad", code: "222222" }] } : undefined));
    render(<MobilePairDialog />);
    await waitFor(() => expect(screen.getByLabelText("验证码").textContent).toBe("222222"));
  });

  it("到上限: 列出旧手机 (最久没用的默认选中), 替换并允许", async () => {
    invoke.mockImplementation((cmd: string) => Promise.resolve(cmd === "mobile_status" ? {
      pending: [],
      phones: [
        { device: "new", name: "常用手机", publicKey: "", pairedAt: "2026-10-01T00:00:00Z", lastSeen: "2026-11-01T00:00:00Z" },
        { device: "old", name: "旧手机", publicKey: "", pairedAt: "2026-06-01T00:00:00Z" },
      ],
    } : undefined));
    render(<MobilePairDialog />);
    await waitFor(() => expect(handlers["mobile://pair-request"]).toBeTruthy());
    act(() => handlers["mobile://pair-request"]({
      payload: { device: "phone-9", name: "新iPhone", code: "333333", overLimit: true, maxPhones: 2 } }));
    await waitFor(() => expect(screen.getByText("旧手机")).toBeTruthy());
    fireEvent.click(screen.getByText("数字一样, 替换并允许"));
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith("mobile_pair_decide", { device: "phone-9", allow: true, replace: "old" }));
  });

  it("超上限说清楚", async () => {
    render(<MobilePairDialog />);
    await waitFor(() => expect(handlers["mobile://pair-failed"]).toBeTruthy());
    act(() => handlers["mobile://pair-failed"]({ payload: { reason: "已经配了 3 台手机, 到了单位设置的上限。" } }));
    expect(screen.getByRole("alert").textContent).toContain("上限");
  });
});
