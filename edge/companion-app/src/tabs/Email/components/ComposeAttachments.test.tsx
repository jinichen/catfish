// @vitest-environment jsdom
// 10/29: 写邮件加附件 (鸿波「新建缺少增加附件的能力」) —— 选文件、去重、超限提醒、存草稿时带上、改草稿保留旧附件。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { pick, createDraft } = vi.hoisted(() => ({
  pick: vi.fn(),
  createDraft: vi.fn(async () => JSON.stringify({ draft_id: "imap|Drafts|1|9" })),
}));
vi.mock("../../../lib/tauri", () => ({
  emailPickAttachments: pick,
  emailCreateDraft: createDraft,
  emailSendMessage: vi.fn(),
  emailReadMessage: vi.fn(),
  emailAttachmentPreview: vi.fn(),
  getPickerModel: vi.fn(),
}));

import ComposeCore from "./ComposeCore";
import { mergePicked, MAX_ATTACHMENTS_BYTES } from "./ComposeAttachments";

const docx = { path: "/Users/x/结算单-已修正.docx", name: "结算单-已修正.docx", size: 120 * 1024 };

afterEach(() => {
  cleanup();
  pick.mockReset();
  createDraft.mockClear();
});

function renderCompose(extra: Record<string, unknown> = {}) {
  return render(
    <ComposeCore
      isOpen
      onClose={() => undefined}
      initialTo="ffchenxp@chinatelecom.cn"
      initialSubject="帮我看下这个结算单有没问题，谢谢"
      agentName="小鲶"
      agentPersonality={undefined}
      resetKey="k"
      {...extra}
    />,
  );
}

describe("写邮件的附件", () => {
  it("同一个文件选两次只算一个", () => {
    expect(mergePicked([docx], [docx, { ...docx, path: "/b.pdf", name: "b.pdf" }]).map((a) => a.name)).toEqual([
      "结算单-已修正.docx",
      "b.pdf",
    ]);
  });

  it("添加附件 → 显示文件名和大小 → 存草稿时把路径交给后端", async () => {
    pick.mockResolvedValueOnce([docx]);
    renderCompose();
    await act(async () => {
      fireEvent.click(screen.getByText("📎 添加附件"));
    });
    expect(screen.getByText("📎 结算单-已修正.docx")).toBeTruthy();
    expect(screen.getAllByText(/120 KB/).length).toBeGreaterThan(0);
    await act(async () => {
      fireEvent.click(screen.getByText("💾 仅保存草稿"));
    });
    expect(createDraft).toHaveBeenCalledWith(
      expect.objectContaining({ attachments: ["/Users/x/结算单-已修正.docx"], keepAttachments: [] }),
    );
  });

  it("点 × 去掉附件", async () => {
    pick.mockResolvedValueOnce([docx]);
    renderCompose();
    await act(async () => {
      fireEvent.click(screen.getByText("📎 添加附件"));
    });
    fireEvent.click(screen.getByLabelText("移除附件 结算单-已修正.docx"));
    expect(screen.queryByText("📎 结算单-已修正.docx")).toBeNull();
  });

  it("合计超过上限: 当场提醒, 存草稿 / 发送按钮不可点", async () => {
    pick.mockResolvedValueOnce([{ ...docx, size: MAX_ATTACHMENTS_BYTES + 1 }]);
    renderCompose();
    await act(async () => {
      fireEvent.click(screen.getByText("📎 添加附件"));
    });
    expect(screen.getByText(/多数邮箱会退信/)).toBeTruthy();
    expect((screen.getByText("💾 仅保存草稿") as HTMLButtonElement).disabled).toBe(true);
  });

  it("改草稿: 旧草稿的附件默认保留, 去掉的不再带", async () => {
    renderCompose({
      replacesDraftId: "imap|Drafts|1|5",
      existingAttachments: [
        { filename: "原来的.docx", size_bytes: 2048 },
        { filename: "不要了.pdf", size_bytes: 1024 },
      ],
    });
    expect(screen.getByText("📎 原来的.docx")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("移除附件 不要了.pdf"));
    await act(async () => {
      fireEvent.click(screen.getByText("💾 仅保存草稿"));
    });
    expect(createDraft).toHaveBeenCalledWith(
      expect.objectContaining({ replaces: "imap|Drafts|1|5", keepAttachments: ["原来的.docx"], attachments: [] }),
    );
  });
});
