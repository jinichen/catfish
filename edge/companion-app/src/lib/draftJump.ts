/** 小鲶在对话里存了草稿 → 回合结束后切到邮件页草稿箱, 选中那封 (9/27)。
 *
 * 邮件页里「存草稿就跳过去」早就做了, 但对话里小鲶调 catfish_email_create_draft
 * 存的草稿不跳 —— 员工只看到一句"草稿在草稿箱", 得自己去找。
 *
 * 为什么不在收到工具事件时直接跳:
 *   · hermes 的 `hermes.tool.progress` completed 事件**不带结果**, 不知道成没成功、
 *     草稿 id 是什么。
 *   · 回合还没结束就切走, 小鲶最后那句话员工没看到。
 * 所以: 工具完成时只记一笔; 回合结束后去草稿箱里**实际查一次**, 有一封是这之后
 * 新存的才跳 —— 存失败了就不跳, 不靠猜。
 */
import { emailListFetch, type EmailDigestItem } from "./tauri";
import { useUIStore } from "../store/ui";

const DRAFT_TOOL = /catfish_email_create_draft$/;
/** 草稿的 Date 头是存的那一刻; 留出本机时钟和服务器处理的余量 */
const FRESH_WINDOW_MS = 3 * 60 * 1000;

let pendingSince: number | null = null;

/** chat.ts 收到 hermes.tool.progress 时调。 */
export function noteToolProgress(tool: unknown, status: unknown): void {
  if (status === "completed" && typeof tool === "string" && DRAFT_TOOL.test(tool)) {
    pendingSince ??= Date.now();
  }
}

export function pickFreshDraft(list: EmailDigestItem[], since: number): EmailDigestItem | null {
  const fresh = list
    .filter((d) => Date.parse(d.date) >= since - FRESH_WINDOW_MS)
    .sort((a, b) => Date.parse(b.date) - Date.parse(a.date));
  return fresh[0] ?? null;
}

/** 回合结束时调 (sendMessage 的 finally)。员工不在对话页就不打扰。 */
export async function jumpToFreshDraft(): Promise<void> {
  const since = pendingSince;
  pendingSince = null;
  if (since === null || useUIStore.getState().activeTab !== "chat") return;
  try {
    const list = JSON.parse(await emailListFetch(false, 20, "Drafts"));
    const draft = Array.isArray(list) ? pickFreshDraft(list as EmailDigestItem[], since) : null;
    if (draft) useUIStore.getState().openEmailDraft(draft.id);
  } catch (e) {
    console.warn("[draftJump] 查草稿箱失败, 不跳转:", e);
  }
}
