/**
 * @vitest-environment jsdom
 */
/** 11/18 鸿波「邮件少了转发功能」: 详情页「转发」—— 收件人空着、主题「转发: …」、正文只写附言,
 *  原文和附件由后台接上 (catfish-email draft --forward); 不当回复串 (不带 in-reply-to)。 */
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
import { _buildForwardSubject } from "./helpers";

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

describe("转发", () => {
  it("主题加「转发:」, 已经是转发的不重复加", () => {
    expect(_buildForwardSubject("周报")).toBe("转发: 周报");
    expect(_buildForwardSubject("Fwd: 周报")).toBe("Fwd: 周报");
    expect(_buildForwardSubject("转发：周报")).toBe("转发：周报");
    expect(_buildForwardSubject("")).toBe("转发: ");
  });

  it("点「转发」: 收件人空、主题转发、写明原文和附件会带上; 存草稿带 forward=原邮件, 不带 in-reply-to", async () => {
    api.create.mockResolvedValue(JSON.stringify({ draft_id: "d1" }));
    await act(async () => {
      render(<DetailPane msg={msg as never} list={[]} onAskCatfish={() => {}} onDeleted={() => {}} />);
    });
    await act(async () => { fireEvent.click(screen.getByText("转发")); });
    expect((screen.getByLabelText("收件人") as HTMLInputElement).value).toBe("");
    expect((screen.getByDisplayValue("转发: 关于补充医疗保险理赔的通知") as HTMLInputElement)).toBeTruthy();
    expect(screen.getByLabelText("转发说明").textContent).toContain("理赔指引.pdf");
    expect(screen.queryByText(/让小鲶.*拟稿|帮我拟稿/)).toBeNull();       // 转发不出「按回复拟稿」
    fireEvent.change(screen.getByLabelText("收件人"), { target: { value: "zhang@example.com" } });
    fireEvent.change(screen.getByPlaceholderText(/附言/), { target: { value: "张工, 请看附件" } });
    await act(async () => { fireEvent.click(screen.getByText("仅保存草稿")); });
    const args = api.create.mock.calls[0][0];
    expect(args.forward).toBe(msg.id);
    expect(args.inReplyTo).toBeUndefined();
    expect(args.body).toBe("张工, 请看附件");
    expect(args.subject).toBe("转发: 关于补充医疗保险理赔的通知");
    expect(args.account).toBe("Chinatelecom");
  });

  it("点「回复」照旧: 带原发件人、Re: 主题、引用原文、in-reply-to, 不带 forward", async () => {
    api.create.mockResolvedValue(JSON.stringify({ draft_id: "d2" }));
    await act(async () => {
      render(<DetailPane msg={msg as never} list={[]} onAskCatfish={() => {}} onDeleted={() => {}} />);
    });
    await act(async () => { fireEvent.click(screen.getByText("回复")); });
    await act(async () => { fireEvent.click(screen.getByText("仅保存草稿")); });
    const args = api.create.mock.calls[0][0];
    expect(args.forward).toBeUndefined();
    expect(args.inReplyTo).toBe(msg.id);
    expect(args.subject).toBe("Re: 关于补充医疗保险理赔的通知");
  });
});
