import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./tauri", () => ({ emailLastDraft: vi.fn() }));
vi.mock("../store/ui", () => {
  const state = { activeTab: "chat", openEmailDraft: vi.fn() };
  return { useUIStore: { getState: () => state } };
});

import { emailLastDraft } from "./tauri";
import { useUIStore } from "../store/ui";
import { jumpToFreshDraft } from "./draftJump";

const lastDraft = emailLastDraft as ReturnType<typeof vi.fn>;
const ui = useUIStore.getState() as unknown as { activeTab: string; openEmailDraft: ReturnType<typeof vi.fn> };

/** 9/29 鸿波: "Windows 版落草稿箱后依旧没有跳转到草稿箱"。
 *  现在认的是邮件组件存草稿时记下的那一笔 (catfish_email/last_draft.py), 不再认工具名、
 *  不再现连服务器按 Date 头猜 —— 小鲶从终端里跑 `catfish-email draft` 存的也算。 */
describe("draftJump", () => {
  beforeEach(() => {
    lastDraft.mockReset();
    ui.openEmailDraft.mockReset();
    ui.activeTab = "chat";
  });

  it("jumps to the draft saved during this round, whoever saved it", async () => {
    const roundStartedAt = Date.now();
    lastDraft.mockResolvedValueOnce({ id: "imap|INBOX.Drafts|1|42", created_at: roundStartedAt / 1000 + 5 });
    await jumpToFreshDraft(roundStartedAt);
    expect(ui.openEmailDraft).toHaveBeenCalledWith("imap|INBOX.Drafts|1|42");
  });

  it("does not jump for a draft saved before this round, or when nothing was saved", async () => {
    const roundStartedAt = Date.now();
    lastDraft.mockResolvedValueOnce({ id: "old", created_at: roundStartedAt / 1000 - 600 });
    await jumpToFreshDraft(roundStartedAt);
    lastDraft.mockResolvedValueOnce(null);
    await jumpToFreshDraft(roundStartedAt);
    expect(ui.openEmailDraft).not.toHaveBeenCalled();
  });

  it("leaves the employee alone once they have left the chat page", async () => {
    ui.activeTab = "knowledge";
    await jumpToFreshDraft(Date.now());
    expect(lastDraft).not.toHaveBeenCalled();
    expect(ui.openEmailDraft).not.toHaveBeenCalled();
  });

  it("a failed read just means no jump", async () => {
    lastDraft.mockRejectedValueOnce(new Error("boom"));
    await expect(jumpToFreshDraft(Date.now())).resolves.toBeUndefined();
    expect(ui.openEmailDraft).not.toHaveBeenCalled();
  });
});
