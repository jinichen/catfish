/**
 * 11/24 鸿波「工作台输入还没发送的条件下, 切换到其他 TAB, 再切回来输入被清空了」:
 * 对话输入框 (tabs/Chat/ChatInput) 的字和附件原来是组件自己的 state —— 切到邮件 / 协同等页, 对话页卸掉, 一起没了。
 *
 * 改成存在组件外面 (这里), 按会话分开: 切页回来还在; 换到别的会话看到的是那个会话自己没发的草稿;
 * 「新对话」(还没有会话号) 用 NEW_KEY。发出去 / 清空就删掉那一份。只在内存里 (附件可能很大, 不落盘), 关掉 Companion 就没了。
 */
import type { Attachment } from "../types/chat";

export const NEW_KEY = "__new__";

export interface ComposerDraft {
  text: string;
  attachments: Attachment[];
}

const EMPTY: ComposerDraft = { text: "", attachments: [] };
const drafts = new Map<string, ComposerDraft>();

export function draftKey(sessionId: string | null | undefined): string {
  return sessionId || NEW_KEY;
}

export function loadDraft(key: string): ComposerDraft {
  return drafts.get(key) ?? EMPTY;
}

export function saveDraft(key: string, d: ComposerDraft): void {
  if (!d.text && d.attachments.length === 0) drafts.delete(key);
  else drafts.set(key, d);
}

/** 测试用 */
export function _clearDraftsForTest(): void {
  drafts.clear();
}
