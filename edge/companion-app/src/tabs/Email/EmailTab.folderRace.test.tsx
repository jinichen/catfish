/**
 * @vitest-environment jsdom
 */
/** 11/4 鸿波截图: 草稿箱里列出了收件箱的信, 收到的通知带着「发送这封草稿」/「删除」。
 * 收件箱全量要 20 秒, 期间切到草稿箱, 草稿箱先回 (空), 收件箱后回 —— 晚到的那份盖掉了草稿箱。 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, cleanup, act, fireEvent } from "@testing-library/react";

let releaseInbox: (v: string) => void = () => undefined;

vi.mock("../../lib/tauri", () => ({
  emailListFetch: vi.fn(async (_unread: boolean, _limit: number, folder?: string) => {
    if (folder === "Sent" || folder === "Drafts") return "[]";
    return new Promise<string>((r) => { releaseInbox = r; }); // 收件箱慢 (实测全量 20 秒)
  }),
  emailAccountsFetch: vi.fn(async () => "[]"),
  emailReadMessage: vi.fn(async () => "{}"),
  emailCheckNew: vi.fn(async () => undefined),
  emailMailDirStatus: vi.fn(async () => "ok"),
  emailPoliticalGet: vi.fn(async () => ({})),
  emailClassifyNow: vi.fn(async () => ({ urgency: {}, actions: {} })),
  emailPhishingScanNow: vi.fn(async () => ({})),
}));

vi.mock("../../store/ui", () => ({
  useUIStore: (sel: (s: unknown) => unknown) =>
    sel({ startEmailChat: vi.fn(), startProactiveChat: vi.fn(), setActiveTab: vi.fn() }),
}));
vi.mock("../../store/agent", () => ({
  useAgentStore: (sel: (s: { name: string; personality: string }) => unknown) =>
    sel({ name: "小鲶", personality: "" }),
}));
vi.mock("../../store/email", () => {
  const snapshot = () => ({
    urgencyMap: {}, actionMap: {}, listCache: null,
    setUrgencyMap: () => undefined,
    reconcileFromRust: async () => ({}),
    markRead: () => undefined,
    setListCache: () => undefined,
    mergeActionMap: () => undefined,
  });
  const hook = (sel: (s: ReturnType<typeof snapshot>) => unknown) => sel(snapshot());
  return { useEmailStore: Object.assign(hook, { getState: snapshot }) };
});

import EmailTab from "./EmailTab";

const inboxMail = JSON.stringify([
  { id: "apple_mail|Work|101", subject: "示例通知: 下周例会改到周三", sender: "notice@example.com",
    account: "Work", date: "2026-01-05T09:00:00", is_read: true },
]);

describe("EmailTab 切文件夹", () => {
  afterEach(() => cleanup());

  it("切到草稿箱后, 晚到的收件箱列表不许显示在草稿箱里", async () => {
    render(<EmailTab />);
    await act(async () => {});
    await act(async () => { fireEvent.click(screen.getByText("草稿箱")); });
    await act(async () => { releaseInbox(inboxMail); });
    expect(screen.queryByText("示例通知: 下周例会改到周三")).toBeNull();
    expect(screen.queryByText("发送这封草稿")).toBeNull();
  });

  it("没切走时收件箱照常显示", async () => {
    render(<EmailTab />);
    await act(async () => {});
    await act(async () => { releaseInbox(inboxMail); });
    expect(screen.getByText("示例通知: 下周例会改到周三")).toBeTruthy();
  });
});
