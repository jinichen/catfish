/** 切到草稿箱并选中某封 —— 两个入口 (9/26, 9/27):
 *   · 邮件页里存了草稿 (新建 / 回复 / 改草稿) → showDraft(id)
 *   · 小鲶在对话里存了草稿 → store.pendingDraftFocus (lib/draftJump.ts 写入),
 *     切到邮件页后这里消费
 *   · 11/15 对话卡片里的邮件卡点「在邮件里打开」→ store.pendingMailFocus: 切到收件箱选中那封 (同一套做法)
 * 从 EmailTab 拆出来: 那个文件已经贴着 800 行红线。
 */
import { useEffect } from "react";
import { useUIStore } from "../../store/ui";
import type { MailFolder } from "./components/FolderTabs";

export function useDraftFocus(opts: {
  folder: MailFolder;
  setFolder: (f: MailFolder) => void;
  clearItems: () => void;
  setSelectedId: (id: string) => void;
  reload: () => void;
}): (id: string) => void {
  const showDraft = (id: string) => {
    opts.setSelectedId(id);
    if (opts.folder === "Drafts") opts.reload();
    else {
      opts.setFolder("Drafts");
      opts.clearItems();
    }
  };
  const pending = useUIStore((s) => s.pendingDraftFocus);
  useEffect(() => {
    if (!pending) return;
    const id = useUIStore.getState().consumeDraftFocus();
    if (id) showDraft(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending]);
  const pendingMail = useUIStore((s) => s.pendingMailFocus);
  useEffect(() => {
    if (!pendingMail) return;
    const id = useUIStore.getState().consumeMailFocus();
    if (!id) return;
    opts.setSelectedId(id);
    if (opts.folder !== "Inbox") {
      opts.setFolder("Inbox");
      opts.clearItems();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingMail]);
  return showDraft;
}
