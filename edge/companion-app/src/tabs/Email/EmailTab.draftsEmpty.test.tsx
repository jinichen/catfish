/**
 * @vitest-environment jsdom
 */
/** 11/4 鸿波第二张截图: 不管等多久, 切到草稿箱看到的都是收件箱。
 * 草稿箱本来就是空的 → 列表空 → 「重启后先铺上次的收件箱」那个钩子见空就铺, 把收件箱缓存铺进了草稿箱。 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, cleanup, act, fireEvent } from "@testing-library/react";

const inboxMailJson = JSON.stringify([
  { id: "apple_mail|Work|101", subject: "示例通知: 下周例会改到周三", sender: "notice@example.com",
    account: "Work", date: "2026-01-05T09:00:00", is_read: true },
]);

vi.mock("../../lib/tauri", () => ({
  emailListFetch: vi.fn(async (_unread: boolean, _limit: number, folder?: string) => {
    if (_unread || folder === "Sent" || folder === "Drafts") return "[]"; // 草稿箱空、没有未读
    return inboxMailJson;
  }),
  emailAccountsFetch: vi.fn(async () => "[]"),
  emailReadMessage: vi.fn(async () => "{}"),
  emailMarkRead: vi.fn(async () => "{}"),
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
    urgencyMap: {}, actionMap: {}, deleted: {},
    listCache: { items: JSON.parse(inboxMailJson), sentItems: [], accounts: [], savedAt: 1 }, // 落盘的收件箱缓存
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

describe("EmailTab 草稿箱是空的", () => {
  afterEach(() => cleanup());

  it("切到草稿箱, 不许把收件箱缓存铺进来", async () => {
    render(<EmailTab />);
    await act(async () => {});
    expect(screen.getByText("示例通知: 下周例会改到周三")).toBeTruthy(); // 收件箱照常
    await act(async () => { fireEvent.click(screen.getByText("草稿箱")); });
    await act(async () => {});
    expect(screen.queryByText("示例通知: 下周例会改到周三")).toBeNull();
  });

  it("勾「仅未读」没有未读时, 也不许铺全部收件箱", async () => {
    render(<EmailTab />);
    await act(async () => {});
    await act(async () => { fireEvent.click(screen.getByLabelText(/仅未读/)); });
    await act(async () => {});
    expect(screen.queryByText("示例通知: 下周例会改到周三")).toBeNull();
  });
});
