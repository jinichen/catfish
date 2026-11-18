/** 让小鲶改纪要的进行中状态, 按会议存 (11/18 鸿波「小鲶改动后没采用, 切到其他 TAB 再切回来, 修改的数据都没了」)。
 *
 * 原来放在 useRevise 的 useState 里: 切到工作台等别的主 tab, 会议页整个卸载, 改出来的提议、说过的话、输入框里打了一半的
 * 全没了; 小鲶正在改时切走, 结果回来也没人接。现在:
 *   · 存在模块里的 Map (切 tab 不丢) + localStorage (重开 Companion 也在; 读写失败不影响, 只是退回只在内存);
 *   · 小鲶在改的那次 (pending) 也记下, 结果回来写进这里并通知正挂着的界面 —— 切走再切回来能接上;
 *   · 带一个纪要签名 (改过的时间 / 模型 / 长度): 纪要已经变了 (重新生成、手动编辑、别处采用) 的旧提议作废, 不往新纪要上套。
 */
import type { Minutes, MinutesRevision } from "../../lib/tauri_meeting";

export interface Turn { instruction: string; reply: string; sources?: MinutesRevision["sources"] }

export interface ReviseState {
  sig: string;
  draft: string;
  proposal: MinutesRevision | null;
  turns: Turn[];
  /** 正在等小鲶的那句 (切走再回来显示「小鲶在改…」) */
  pending: string | null;
}

const KEY = (id: string) => `catfish.minutesRevise.${id}`;
const mem = new Map<string, ReviseState>();
const subs = new Set<(meetingId: string) => void>();

export function minutesSig(m: Minutes | null): string {
  return m ? `${m.json.edited_at ?? ""}|${m.json.model ?? ""}|${m.json.template?.id ?? ""}|${m.markdown.length}` : "";
}

export function emptyState(sig: string): ReviseState {
  return { sig, draft: "", proposal: null, turns: [], pending: null };
}

/** 这场会议存着的状态; 纪要已经变了 (签名对不上) 就当没有, 顺手清掉。输入框里打了一半的字保留。 */
export function loadRevise(meetingId: string, sig: string): ReviseState {
  let s = mem.get(meetingId);
  if (!s) {
    try {
      const raw = localStorage.getItem(KEY(meetingId));
      if (raw) s = JSON.parse(raw) as ReviseState;
    } catch {
      s = undefined;
    }
  }
  if (!s) return emptyState(sig);
  // 纪要还没读回来 (刚挂上, sig 是空的): 先别判作废 —— 不然一挂上就把存着的提议当成过期的清掉 (切回来什么都没了)
  if (!sig) return { ...emptyState(s.sig), ...s };
  if (s.sig !== sig) {
    const kept = { ...emptyState(sig), draft: s.draft };
    saveRevise(meetingId, kept);
    return kept;
  }
  return { ...emptyState(sig), ...s };
}

export function saveRevise(meetingId: string, s: ReviseState): void {
  const empty = !s.draft && !s.proposal && !s.turns.length && !s.pending;
  // 清空也在内存里留一份空的: 不能只删内存再指望 localStorage 删成功 —— 删不掉时下次会把旧提议读回来
  mem.set(meetingId, s);
  try {
    if (empty) localStorage.removeItem(KEY(meetingId));
    else localStorage.setItem(KEY(meetingId), JSON.stringify(s));
  } catch {
    /* 存不了 (隐私模式 / 满了): 只在内存里, 切 tab 照样不丢 */
  }
  subs.forEach((f) => f(meetingId));
}

export function subscribeRevise(f: (meetingId: string) => void): () => void {
  subs.add(f);
  return () => void subs.delete(f);
}

/** 测试用 */
export function _clearReviseMemory(): void {
  mem.clear();
}
