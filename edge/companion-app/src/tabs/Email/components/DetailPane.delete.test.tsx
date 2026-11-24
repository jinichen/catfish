/**
 * @vitest-environment jsdom
 */
/** 11/18 鸿波「选择删除邮件, 确认删除按钮换行了」: 点一下删除不许变宽 (变宽就把自己挤到下一行);
 *  跟「标为钓鱼」一组靠右, 要换行也一起换。 (原: 11/18 鸿波「邮件少了转发功能」: 详情页「转发」—— 收件人空着、主题「转发: …」、正文只写附言,
 *  原文和附件由后台接上 (catfish-email draft --forward); 不当回复串 (不带 in-reply-to)。) */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

const api = vi.hoisted(() => ({ create: vi.fn(), send: vi.fn() }));
vi.mock("../../../lib/tauri", () => ({
  emailDeleteMessage: vi.fn(async () => undefined),
  emailPhishingGet: vi.fn(async () => null),
  emailPoliticalScanNow: vi.fn(async () => null),
  emailExportAttachment: vi.fn(async () => ""),
  openFile: vi.fn(async () => undefined),
  emailCreateDraft: api.create,
  emailSendMessage: api.send,
  emailReadMessage: vi.fn(async () => null),
  emailAttachmentPreview: vi.fn(async () => null),
  getPickerModel: vi.fn(async () => ""),
  wikiSearch: vi.fn(async () => ({ matches: [] })),
}));
vi.mock("../../../store/agent", () => ({
  useAgentStore: (sel: (s: { name: string; personality: string }) => unknown) => sel({ name: "小鲶", personality: "" }),
}));

import DetailPane from "./DetailPane";

const msg = {
  id: "imap_chinatelecom|INBOX|1|42",
  account: "Chinatelecom",
  folder: "Inbox",
  subject: "关于补充医疗保险理赔的通知",
  sender: "ff_ffcshr <ff_ffcshr@chinatelecom.cn>",
  recipients: ["ff_allgroup@chinatelecom.cn"],
  cc: [],
  date: "2026-11-17T15:32:00",
  is_read: true,
  has_attachments: true,
  attachments: [{ filename: "理赔指引.pdf", size_bytes: 120000, content_type: "application/pdf" }],
  body_text: "各位领导、同事, 大家好",
  message_id: "<x@chinatelecom.cn>",
  in_reply_to: null,
  references: null,
};

afterEach(() => { cleanup(); api.create.mockReset(); api.send.mockReset(); });

describe("删除按钮", () => {
  it("点一下变「确认删除」: 宽度固定、不折行, 跟标为钓鱼同一组", async () => {
    await act(async () => {
      render(<DetailPane msg={msg as never} list={[]} onAskCatfish={() => {}} onDeleted={() => {}} />);
    });
    const btn = screen.getByText("删除") as HTMLButtonElement;
    const before = { minWidth: btn.style.minWidth, ws: btn.style.whiteSpace };
    await act(async () => { fireEvent.click(btn); });
    expect(btn.textContent).toBe("确认删除");
    expect(btn.style.minWidth).toBe(before.minWidth);
    expect(before.minWidth).toBe("100px");
    expect(btn.style.whiteSpace).toBe("nowrap");
    const group = btn.parentElement as HTMLElement;
    expect(group.style.marginLeft).toBe("auto");
    expect(group.style.flexShrink).toBe("0");
  });
});
