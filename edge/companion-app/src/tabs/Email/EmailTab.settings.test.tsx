/**
 * @vitest-environment jsdom
 */
/** 邮箱设置 (IMAP / 服务器上保留多久 / 档案进度) 的入口, 收件箱有信时也得找得到。
 *
 * 9/28 鸿波: "服务器的邮件保存时间在哪里配置, 我始终找不到"。那张卡片以前只在
 * 收件箱为空或拉取失败时出现 —— 正常用着的人永远看不到它。
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, cleanup, act, fireEvent } from "@testing-library/react";

vi.mock("../../lib/tauri", () => ({
  emailListFetch: vi.fn(async (_u: boolean, _l: number, folder?: string) =>
    folder === "Sent" ? "[]" : JSON.stringify([
      { id: "id-0", subject: "一封信", sender: "a@example.cn", account: "me@example.cn",
        date: "2026-09-18T13:05:57", is_read: false },
    ])),
  emailAccountsFetch: vi.fn(async () => JSON.stringify([
    { name: "Google", address: "me@gmail.com", is_default: true, client: "apple_mail" },
    { name: "Chinatelecom", address: "me@example.cn", is_default: false, client: "apple_mail" },
  ])),
  emailReadMessage: vi.fn(async () => "{}"),
  emailCheckNew: vi.fn(async () => undefined),
  emailMailDirStatus: vi.fn(async () => "ok"),
  emailPoliticalGet: vi.fn(async () => ({})),
  emailClassifyNow: vi.fn(async () => ({ urgency: {}, actions: {} })),
  emailPhishingScanNow: vi.fn(async () => ({})),
}));
vi.mock("./components/ImapSetup", () => ({ default: () => <div>IMAP 卡片</div> }));
vi.mock("./components/ArchivePanel", () => ({ default: () => <div>档案进度</div> }));
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

describe("EmailTab 邮箱设置入口", () => {
  afterEach(() => { cleanup(); });

  it("收件箱有信时也能打开 IMAP 设置和档案进度", async () => {
    render(<EmailTab />);
    await act(async () => {});
    expect(screen.getByText("一封信")).toBeTruthy();
    expect(screen.queryByText("IMAP 卡片")).toBeNull(); // 默认收着, 不占列表的地方

    fireEvent.click(screen.getByText("邮箱设置"));
    expect(screen.getByText("IMAP 卡片")).toBeTruthy();
    expect(screen.getByText("档案进度")).toBeTruthy();
    // 9/28: 先说正在读哪些邮箱 —— Mac 上用「邮件」App 的人不该只看到一张 IMAP 卡片
    expect(screen.getByText(/Google \(me@gmail.com\)、Chinatelecom \(me@example.cn\)/)).toBeTruthy();
    expect(screen.getByText(/只对下面「邮箱直连 \(IMAP\)」的邮箱生效/)).toBeTruthy();

    fireEvent.click(screen.getByText("收起设置"));
    expect(screen.queryByText("IMAP 卡片")).toBeNull();
  });
});
