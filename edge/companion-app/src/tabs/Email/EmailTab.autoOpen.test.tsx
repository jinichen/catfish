/**
 * @vitest-environment jsdom
 */
/** 进邮件页自动打开第一封 (11/11, 1.2.147) —— 在整个 EmailTab 里走一遍:
 *  · 第一封的正文读出来, 但读的时候不让客户端标已读
 *  · 停够 3 秒才标; 点另一封就照旧, 读的时候顺手标掉
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, act, fireEvent } from "@testing-library/react";

const t = vi.hoisted(() => ({
  read: vi.fn(async (id: string) => JSON.stringify({ id, subject: `正文-${id}`, body: "", is_read: false })),
  mark: vi.fn(async () => "{}"),
}));

vi.mock("../../lib/tauri", () => ({
  emailListFetch: vi.fn(async (_u: boolean, _l: number, folder?: string) => folder === "Sent" ? "[]" : JSON.stringify([
    { id: "m1", subject: "第一封", sender: "a@x.cn", account: "me@x.cn", date: "2026-11-11T09:00:00", is_read: false },
    { id: "m2", subject: "第二封", sender: "b@x.cn", account: "me@x.cn", date: "2026-11-10T09:00:00", is_read: false },
  ])),
  emailAccountsFetch: vi.fn(async () => "[]"),
  emailReadMessage: t.read,
  emailMarkRead: t.mark,
  emailCheckNew: vi.fn(async () => undefined),
  emailMailDirStatus: vi.fn(async () => "ok"),
  emailPoliticalGet: vi.fn(async () => ({})),
  emailClassifyNow: vi.fn(async () => ({ urgency: {}, actions: {} })),
  emailPhishingScanNow: vi.fn(async () => ({})),
}));
vi.mock("../../store/ui", () => ({
  useUIStore: (sel: (s: unknown) => unknown) =>
    sel({ startEmailChat: vi.fn(), startProactiveChat: vi.fn(), setActiveTab: vi.fn(), pendingDraftFocus: null }),
}));
vi.mock("../../store/agent", () => ({
  useAgentStore: (sel: (s: { name: string; personality: string }) => unknown) => sel({ name: "小鲶", personality: "" }),
}));
vi.mock("../../store/email", () => {
  const snapshot = () => ({
    urgencyMap: {}, actionMap: {}, listCache: null,
    setUrgencyMap: () => undefined, reconcileFromRust: async () => ({}), markRead: () => undefined,
    setListCache: () => undefined, mergeActionMap: () => undefined, markDeleted: () => undefined,
  });
  const hook = (sel: (s: ReturnType<typeof snapshot>) => unknown) => sel(snapshot());
  return { useEmailStore: Object.assign(hook, { getState: snapshot }) };
});

import EmailTab from "./EmailTab";

async function tick(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

describe("EmailTab 自动打开第一封", () => {
  beforeEach(() => { vi.clearAllMocks(); vi.useFakeTimers(); });
  afterEach(() => { cleanup(); vi.useRealTimers(); });

  it("进来就显示第一封, 停够 3 秒才标已读", async () => {
    render(<EmailTab />);
    await tick(0);
    await tick(0);
    expect(t.read).toHaveBeenCalledWith("m1", { markRead: false });
    expect(screen.queryByText(/选一封邮件/)).toBeNull();
    await tick(2500);
    expect(t.mark).not.toHaveBeenCalled();
    await tick(600);
    expect(t.mark).toHaveBeenCalledWith("m1", true);
  });

  it("3 秒内点了另一封: 第一封保持未读, 点的那封读时顺手标", async () => {
    render(<EmailTab />);
    await tick(0);
    await tick(0);
    await tick(1000);
    fireEvent.click(screen.getByText("第二封"));
    await tick(0);
    expect(t.read).toHaveBeenLastCalledWith("m2", undefined);
    await tick(5000);
    expect(t.mark.mock.calls.some((c) => (c as unknown[])[0] === "m1")).toBe(false);
  });
});
