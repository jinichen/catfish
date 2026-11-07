/** 钓鱼标注的前端状态 (11/07): 标注表 + 标完重扫回来的新结果 (列表 / 详情优先用它)。
 *
 * 订阅即从 Rust 取一次标注表。标 / 撤销: 先存标注 (界面马上按标注显示), 再在后台按新标注重扫这个发件人的那几封,
 * 重扫回来的结果放进 rescanned, 列表和详情都优先用 —— 不用等下次进邮件页。
 */
import { create } from "zustand";

import { sameSender } from "../lib/phishingMarks";
import type { EmailDigestItem } from "../lib/tauri_cron";
// 直接从 tauri_services 取 (不经 lib/tauri 汇总): 很多测试整块替换 lib/tauri, 标注取不到时这里当「没标注」
import {
  emailPhishingMark, emailPhishingMarks, emailPhishingScanNow,
  type PhishingMarks, type PhishingScanResult,
} from "../lib/tauri_services";

interface MarksState {
  marks: PhishingMarks | null;
  /** 标完后台重扫回来的结果 (id → 结果) */
  rescanned: Record<string, PhishingScanResult>;
  load: () => Promise<void>;
  /** verdict = null 撤销。list: 当前列表 (找同一个发件人的那几封重扫) */
  mark: (sender: string, verdict: "safe" | "phishing" | null, subject: string, list: EmailDigestItem[]) => Promise<void>;
}

let loading: Promise<void> | null = null;

export const usePhishingMarksStore = create<MarksState>((set) => ({
  marks: null,
  rescanned: {},
  load: () => {
    loading ??= emailPhishingMarks()
      .then((marks) => set({ marks }))
      .catch(() => set({ marks: { senders: {} } }))
      .finally(() => { loading = null; });
    return loading;
  },
  mark: async (sender, verdict, subject, list) => {
    const items = sameSender(list, sender);
    const { marks } = await emailPhishingMark(sender, verdict, subject, items.map((it) => it.id));
    set({ marks });
    if (!items.length) return;
    // 后台按新标注重扫 (要调模型, 可能几十秒); 失败就等下次进邮件页
    void emailPhishingScanNow(items.map((it) => ({ id: it.id, subject: it.subject ?? "", sender: it.sender ?? "" })))
      .then((store) => set((s) => ({
        rescanned: { ...s.rescanned, ...Object.fromEntries(items.filter((it) => store[it.id]).map((it) => [it.id, store[it.id]])) },
      })))
      .catch(() => {});
  },
}));
