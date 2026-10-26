// @vitest-environment jsdom
/** 等批准横幅按会话显示 (10/26): 自己的显卡片, 别的对话只提一句「去看看」; 「批准并继续」发回这段对话。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const toolBridgeChatApproval = vi.fn();
vi.mock("../lib/tauri", () => ({
  toolBridgeChatApproval: (...a: unknown[]) => toolBridgeChatApproval(...a),
  sendNotification: vi.fn(() => Promise.resolve()),
  hermesApprovalTimeout: vi.fn(() => Promise.resolve(1800)),
}));

import { ApprovalBanner, ApprovalElsewhereBanner } from "./ApprovalBanner";
import { expireItems, receiveApproval, resetApprovalsForTest, useApprovalsStore } from "../store/approvals";
import { useUIStore } from "../store/ui";

const detail = (key: string, session: string | null, command: string) => ({
  approval_session_key: key, command, description: "", pattern_key: "script", timeout_s: 1800, session_id: session,
});

beforeEach(() => {
  resetApprovalsForTest();
  toolBridgeChatApproval.mockReset().mockResolvedValue({});
  useUIStore.setState({ activeTab: "chat", pendingSessionFocus: null });
});
afterEach(cleanup);

describe("ApprovalBanner", () => {
  it("只显这段对话的; 别的对话的提一句, 点「去看看」切过去", () => {
    act(() => {
      receiveApproval(detail("run-a", "A", "python3 a.py"));
      receiveApproval(detail("run-b", "B", "python3 b.py"));
    });
    render(<ApprovalBanner sessionId="A" />);
    expect(screen.getByText("python3 a.py")).toBeTruthy();
    expect(screen.queryByText("python3 b.py")).toBeNull();
    expect(screen.getByText("另外有 1 步在别的对话里等你批准")).toBeTruthy();
    expect(screen.getByText("30:00")).toBeTruthy();
    fireEvent.click(screen.getByText("去看看"));
    expect(useUIStore.getState().pendingSessionFocus).toBe("B");
  });

  it("批准送的是这一条; 另一段对话的还在", async () => {
    act(() => {
      receiveApproval(detail("run-a", "A", "python3 a.py"));
      receiveApproval(detail("run-b", "B", "python3 b.py"));
    });
    render(<ApprovalBanner sessionId="A" />);
    await act(async () => {
      fireEvent.click(screen.getByText("批准"));
    });
    expect(toolBridgeChatApproval).toHaveBeenCalledWith("run-a", "once");
    expect(useApprovalsStore.getState().items.map((i) => i.key)).toEqual(["run-b"]);
  });

  it("作废后「批准并继续」只在那段对话里给, 发进这段对话", () => {
    act(() => {
      receiveApproval(detail("run-a", "A", "python3 a.py"));
      useApprovalsStore.setState(expireItems(useApprovalsStore.getState(), Date.now() + 1_801_000));
    });
    const sentInB = vi.fn();
    const { unmount } = render(<ApprovalBanner sessionId="B" onRetry={sentInB} />);
    expect(screen.queryByText("批准并继续")).toBeNull();
    expect(screen.getByText("别的对话里有一步等批准超时作废了")).toBeTruthy();
    unmount();

    const sentInA = vi.fn();
    render(<ApprovalBanner sessionId="A" onRetry={sentInA} />);
    fireEvent.click(screen.getByText("批准并继续"));
    expect(sentInA).toHaveBeenCalledWith(expect.stringContaining("python3 a.py"));
    expect(sentInB).not.toHaveBeenCalled();
    expect(useApprovalsStore.getState().notes).toHaveLength(0);
  });

  it("别的页面顶上说一声, 点「去处理」打开那段对话", () => {
    act(() => receiveApproval(detail("run-b", "B", "python3 b.py")));
    useUIStore.setState({ activeTab: "dashboard" });
    render(<ApprovalElsewhereBanner />);
    expect(screen.getByText(/小鲶有 1 步在等你批准/)).toBeTruthy();
    fireEvent.click(screen.getByText("去处理"));
    expect(useUIStore.getState()).toMatchObject({ activeTab: "chat", pendingSessionFocus: "B" });
  });
});
