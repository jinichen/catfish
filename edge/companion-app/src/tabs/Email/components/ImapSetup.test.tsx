/**
 * @vitest-environment jsdom
 */
/** 邮箱直连 (IMAP) 卡片: 服务器上的邮件保留多久。
 *
 * 9/28 鸿波: "服务器的邮件保存时间在哪里配置, 我始终找不到"。
 * 两个原因: 卡片只在收件箱为空时出现 (EmailTab 那边加了常驻入口);
 * 就算找到了, 改这一项也只能「重新配置」—— 邮箱、服务器、密码全部重填。
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getImapStatus: vi.fn(),
  setImapRetention: vi.fn(),
  saveImapCredential: vi.fn(),
  clearImapCredential: vi.fn(),
  getMailAccountSettings: vi.fn(async () => []),
}));
vi.mock("../../../lib/tauri_imap", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../lib/tauri_imap")>()),
  ...mocks,
}));

import ImapSetup from "./ImapSetup";

const SAVED = {
  configured: true,
  host: "imap.example.cn",
  user: "me@example.cn",
  port: 993,
  password_present: true,
  smtp_host: "",
  smtp_port: 0,
  retention: "never",
};

async function renderConfigured() {
  mocks.getImapStatus.mockResolvedValue(SAVED);
  render(<ImapSetup />);
  await act(async () => {});
}

describe("ImapSetup · 服务器上保留多久", () => {
  beforeEach(() => { vi.clearAllMocks(); });
  afterEach(() => { cleanup(); });

  it("配好之后就地能改, 不用重填密码", async () => {
    await renderConfigured();
    mocks.setImapRetention.mockResolvedValue({ ...SAVED, retention: "2w" });

    const select = screen.getByLabelText("服务器上的邮件保留多久") as HTMLSelectElement;
    expect(select.value).toBe("never");
    expect(screen.queryByText("保存")).toBeNull(); // 没改就没有保存按钮

    fireEvent.change(select, { target: { value: "2w" } });
    expect(screen.getByText(/不可恢复/)).toBeTruthy(); // 会删的档位要当场说清楚
    await act(async () => { fireEvent.click(screen.getByText("保存")); });

    expect(mocks.setImapRetention).toHaveBeenCalledWith("2w");
    expect(mocks.saveImapCredential).not.toHaveBeenCalled(); // 不走整套保存 (那要密码)
    expect(screen.queryByText("保存")).toBeNull();
  });

  it("「重新配置」带上已存的邮箱和服务器, 只需重填密码", async () => {
    await renderConfigured();
    fireEvent.click(screen.getByText("重新配置"));
    expect(screen.getByDisplayValue("me@example.cn")).toBeTruthy();
    expect(screen.getByDisplayValue("imap.example.cn")).toBeTruthy();
    expect(screen.getByDisplayValue("993")).toBeTruthy();
  });

  it("能从「邮件」App 带入服务器和用户名, 密码不带", async () => {
    mocks.getImapStatus.mockResolvedValue({ ...SAVED, configured: false });
    mocks.getMailAccountSettings.mockResolvedValue([
      { name: "Chinatelecom", address: "me@example.cn", host: "imap.example.cn", user: "me@example.cn",
        port: 993, ssl: true, protocol: "imap", client: "apple_mail" },
      { name: "Old", address: "old@example.cn", host: "pop.example.cn", user: "old",
        port: 995, ssl: true, protocol: "pop", client: "apple_mail" },
    ]);
    render(<ImapSetup />);
    await act(async () => {});
    await act(async () => { fireEvent.click(screen.getByText("配置")); });

    const pick = screen.getByLabelText("从邮件 App 带入账号设置") as HTMLSelectElement;
    expect(pick.options.length).toBe(2); // 提示项 + 一个 IMAP 账号; POP 的不列
    fireEvent.change(pick, { target: { value: "0" } });
    expect(screen.getByDisplayValue("me@example.cn")).toBeTruthy();
    expect(screen.getByDisplayValue("imap.example.cn")).toBeTruthy();
  });
});
