/**
 * @vitest-environment jsdom
 */
/** 邮箱设置里什么时候出现 IMAP (9/28 鸿波: "用原生客户端为什么还提示 IMAP, 是不是很奇怪?")。 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./ImapSetup", () => ({ default: () => <div>IMAP 卡片</div> }));
vi.mock("./ArchivePanel", () => ({ default: () => null }));

import MailSettingsPanel from "./MailSettingsPanel";

const mac = { name: "Google", address: "me@gmail.com", is_default: true, client: "apple_mail" };
const imap = { name: "me@example.cn", address: "me@example.cn", is_default: false, client: "imap" };

describe("MailSettingsPanel", () => {
  afterEach(() => { cleanup(); });

  it("只靠邮件客户端读信: 只列账号, 不出现 IMAP 和保留时间", () => {
    render(<MailSettingsPanel accounts={[mac]} onChanged={() => {}} />);
    expect(screen.getByText(/Google \(me@gmail.com\)/)).toBeTruthy();
    expect(screen.queryByText("IMAP 卡片")).toBeNull();
    expect(screen.queryByText(/保留多久/)).toBeNull();
  });

  it("配了 IMAP: 卡片在 (能看、能改保留时间), 并说明保留时间只管它", () => {
    render(<MailSettingsPanel accounts={[mac, imap]} onChanged={() => {}} />);
    expect(screen.getByText("IMAP 卡片")).toBeTruthy();
    expect(screen.getByText(/只对下面邮箱直连 \(IMAP\) 的邮箱生效/)).toBeTruthy();
  });

  it("没有客户端账号 (Windows): IMAP 就是唯一的路, 照常显示", () => {
    render(<MailSettingsPanel accounts={[]} onChanged={() => {}} />);
    expect(screen.getByText("IMAP 卡片")).toBeTruthy();
  });
});
