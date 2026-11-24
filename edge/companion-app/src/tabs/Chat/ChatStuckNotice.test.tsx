// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a) }));

import ChatStuckNotice, { isStuckSession } from "./ChatStuckNotice";
import { useChatStore } from "../../store/chat";

beforeEach(() => {
  invoke.mockReset();
  useChatStore.setState({ persistedSessionId: "20261106_142422_babcd2" });
});

describe("对话被卡住的那一轮 (11/24 unknown_execution)", () => {
  it("认得 SSE 里的和 409 里的", () => {
    expect(isStuckSession("unknown_execution")).toBe(true);
    expect(isStuckSession('HTTP 409: {"error":{"code":"unknown_execution"}}')).toBe(true);
    expect(isStuckSession("上游内部错误 (500)")).toBe(false);
  });

  it("点按钮放弃那一轮, 说清楚排着的没执行", async () => {
    invoke.mockResolvedValue({ discarded: 1, cancelled: 6 });
    render(<ChatStuckNotice />);
    expect(screen.getByText(/这个对话卡住了/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "放弃卡住的那一轮" }));
    await waitFor(() => expect(screen.getByText(/已经解开了, 卡住期间排着的 6 条没有执行/)).toBeTruthy());
    expect(invoke).toHaveBeenCalledWith("hermes_session_unstick", { sessionId: "20261106_142422_babcd2" });
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("失败时原样告诉人, 按钮还在可以再点", async () => {
    invoke.mockRejectedValue("本机服务没接受: not_found");
    render(<ChatStuckNotice />);
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(screen.getByText(/没解开: 本机服务没接受/)).toBeTruthy());
    expect(screen.getByRole("button", { name: "放弃卡住的那一轮" })).toBeTruthy();
  });
});
