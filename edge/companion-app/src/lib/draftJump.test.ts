import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./tauri", () => ({ emailLastDraft: vi.fn(), emailRecentDraft: vi.fn() }));
const chat = { messages: [] as { role: string; content: string }[] };
vi.mock("../store/chat", () => ({ useChatStore: { getState: () => chat } }));
vi.mock("../store/ui", () => {
  const state = { activeTab: "chat", openEmailDraft: vi.fn() };
  return { useUIStore: { getState: () => state } };
});

import { emailLastDraft, emailRecentDraft } from "./tauri";
import { useUIStore } from "../store/ui";
import { jumpToFreshDraft } from "./draftJump";

const lastDraft = emailLastDraft as ReturnType<typeof vi.fn>;
const recentDraft = emailRecentDraft as ReturnType<typeof vi.fn>;
const ui = useUIStore.getState() as unknown as { activeTab: string; openEmailDraft: ReturnType<typeof vi.fn> };

/** 9/29 鸿波: "Windows 版落草稿箱后依旧没有跳转到草稿箱"。
 *  现在认的是邮件组件存草稿时记下的那一笔 (catfish_email/last_draft.py), 不再认工具名、
 *  不再现连服务器按 Date 头猜 —— 小鲶从终端里跑 `catfish-email draft` 存的也算。 */
describe("draftJump", () => {
  beforeEach(() => {
    lastDraft.mockReset();
    recentDraft.mockReset();
    chat.messages = [];
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

  // 11/10 鸿波: 「邮件落草稿箱后没有跳到草稿邮件」—— 小鲶用自己的脚本直接在 Mail 里建草稿, 没有那一笔
  describe("没有那一笔时的兜底", () => {
    it("小鲶最后说了草稿 → 问邮件客户端这一回合期间新存的, 有就跳", async () => {
      const roundStartedAt = Date.now() - 40_000;
      lastDraft.mockResolvedValueOnce({ id: "old", created_at: roundStartedAt / 1000 - 3600 });
      chat.messages = [{ role: "user", content: "带附件放草稿箱" }, { role: "assistant", content: "已落进 Chinatelecom 草稿箱，附件 1 个。" }];
      recentDraft.mockResolvedValueOnce({ id: "apple_mail|Chinatelecom|3542", created_at: Date.now() / 1000 - 10 });
      await jumpToFreshDraft(roundStartedAt);
      expect(recentDraft).toHaveBeenCalledTimes(1);
      const maxAge = recentDraft.mock.calls[0][0] as number;
      expect(maxAge).toBeGreaterThanOrEqual(40);   // 覆盖整个回合
      expect(maxAge).toBeLessThanOrEqual(50);      // 但不往回多看, 免得跳到更早的草稿
      expect(ui.openEmailDraft).toHaveBeenCalledWith("apple_mail|Chinatelecom|3542");
    });

    it("最后那句没提草稿 → 不去问 (不是每个回合都值得等邮件客户端几秒)", async () => {
      lastDraft.mockResolvedValueOnce(null);
      chat.messages = [{ role: "assistant", content: "今天的日程整理好了。" }];
      await jumpToFreshDraft(Date.now());
      expect(recentDraft).not.toHaveBeenCalled();
      expect(ui.openEmailDraft).not.toHaveBeenCalled();
    });

    it("问了但没有新存的 → 不跳; 有那一笔时不多问", async () => {
      lastDraft.mockResolvedValueOnce(null);
      chat.messages = [{ role: "assistant", content: "草稿内容如下，你看看要不要我存进草稿箱？" }];
      recentDraft.mockResolvedValueOnce(null);
      await jumpToFreshDraft(Date.now());
      expect(ui.openEmailDraft).not.toHaveBeenCalled();

      const roundStartedAt = Date.now();
      lastDraft.mockResolvedValueOnce({ id: "fresh", created_at: roundStartedAt / 1000 + 2 });
      recentDraft.mockClear();
      await jumpToFreshDraft(roundStartedAt);
      expect(recentDraft).not.toHaveBeenCalled();
      expect(ui.openEmailDraft).toHaveBeenCalledWith("fresh");
    });

    it("查的那几秒里员工切走了 → 不把他拽回来", async () => {
      lastDraft.mockResolvedValueOnce(null);
      chat.messages = [{ role: "assistant", content: "已存进草稿箱。" }];
      recentDraft.mockImplementationOnce(async () => { ui.activeTab = "wiki"; return { id: "d", created_at: 1 }; });
      await jumpToFreshDraft(Date.now());
      expect(ui.openEmailDraft).not.toHaveBeenCalled();
    });
  });
});
