/**
 * 11/24 右栏「待处理」: 这个会话里放到右栏的卡片 (要填 / 大件的, lib/a2uiCard.ts cardPlacement)。
 *
 * 对话里的那一行 (ToolCards 的 CardStub) 画出来时登记; 换会话清空。卡片本身还是按编号读文件画 (跟原来一样),
 * 这里只记: 哪几张、标题、交了没有、什么时候出现 (新出来的才自动打开右栏), 以及想看哪一张 (对话里点「在右边」)。
 */
import { create } from "zustand";

export interface PanelCard {
  cardId: string;
  title: string;
  submitted: boolean;
  /** 能不能交 (对话页 = true) */
  interactive: boolean;
  /** 卡片编号里的时间 (c_YYYYMMDD_HHMMSS_…), 毫秒; 排序、判断是不是这一轮新出的 */
  at: number;
}

interface CardPanelState {
  cards: PanelCard[];
  /** 对话里点了「在右边」的那张: 右栏滚过去并闪一下 */
  focus: { cardId: string; n: number } | null;
  register: (c: PanelCard) => void;
  setFocus: (cardId: string) => void;
  reset: () => void;
}

/** c_20261124_093542_37c64e → 2026-11-24 09:35:42 (本机时间) 的毫秒; 认不出 = 0 */
export function cardTime(cardId: string): number {
  const m = /^c_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})_/.exec(cardId);
  if (!m) return 0;
  const [, y, mo, d, h, mi, s] = m.map(Number);
  return new Date(y, mo - 1, d, h, mi, s).getTime();
}

export const useCardPanelStore = create<CardPanelState>((set) => ({
  cards: [],
  focus: null,
  register: (c) =>
    set((s) => {
      const old = s.cards.find((x) => x.cardId === c.cardId);
      if (old && old.title === c.title && old.submitted === c.submitted && old.interactive === c.interactive) return s;
      const rest = s.cards.filter((x) => x.cardId !== c.cardId);
      return { cards: [...rest, c].sort((a, b) => b.at - a.at) };
    }),
  setFocus: (cardId) => set((s) => ({ focus: { cardId, n: (s.focus?.n ?? 0) + 1 } })),
  reset: () => set({ cards: [], focus: null }),
}));
