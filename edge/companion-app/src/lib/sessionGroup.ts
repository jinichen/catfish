/** BL-COMPANION-SESSION-DEDUP (5/20 鸿波): sidebar 同 title 会话堆叠为一组.
 *
 * 鸿波报"会话列表还是出现重复" — 实际是不同 session id 但 LLM 生成 title 撞名,
 * 76 条 / 18 条 message count 不同说明真不是同一 session, 是 title 一样的两次
 * 会话. 堆叠显主条 + "+N 同名" chip + 展开 sub-session 列表.
 *
 * v2 修法 (5/20 鸿波再报"列表没人和变化"): 全 title 严格比对没合并因为
 * "手动测 sync 函数 16:42:13 已完成 v0.1.7" / "...v0.1.8" 后缀不同. sidebar
 * 240px / 13px 字体在 ~22 字符就 ellipsis 截断, 鸿波视觉看着同名期待合并.
 * 改前 22 字符 prefix dedup, 跟渲染截断长度对齐.
 *
 * 算法纯函数, 抽这里方便单测. ChatSidebar 用渲染.
 */

import type { SessionMeta } from "../types/session";

/** 跟 sidebar 240px / 13px 中文字体的 ellipsis 截断长度对齐.
 * 22 中文字 ≈ 视觉一行能看到的字符数. 不同语言长度估计略偏, 但中文场景准.
 * 太短 (e.g. 10) 会误合并 "汇报 5月20" / "汇报 5月21"; 太长 (e.g. 50) 跟全
 * 比对没区别, 解不了 v0.1.7/v0.1.8 撞名. 22 是经验值, 跟视觉截断对齐.
 */
const VISUAL_DEDUP_PREFIX = 22;

export interface SessionGroupEntry {
  /** dedupe key — effective title 前 22 字符 lowercase trim. 同 key 算同 group. */
  key: string;
  /** 主条显示用 title (preserve case + 空格, 取第一条 effective title 全文,
   * 渲染时 sidebar CSS ellipsis 截断 — 跟 dedup key 长度对齐). */
  displayTitle: string;
  /** group 里的所有 sessions, 按 input 顺序 — caller 应先按 started_at desc 排好 */
  sessions: SessionMeta[];
}

/** effective title — 跟 ChatSidebar SessionRow 内部算法对齐.
 * title 没生成时 fallback firstUserMessage, 再 fallback id 前 17 字符. */
export function effectiveTitle(s: SessionMeta): string {
  return (
    s.title?.trim() ||
    s.firstUserMessage?.trim() ||
    `(${s.id.slice(0, 17)})`
  );
}

/** 把 title 折成 dedup key — 前 22 字符 lowercase trim.
 * 暴露成 export 是为了单测 + 极端场景外部调用 (e.g. 给某条新 session 算 key). */
export function dedupKey(title: string): string {
  return title.trim().toLowerCase().slice(0, VISUAL_DEDUP_PREFIX);
}

/** 按 effective title 前缀把 session list 分组. caller 应已按 started_at desc 排过.
 * 同 key 的第一条 (即 group 内最新) 作 displayTitle. */
export function groupSessionsByTitle(list: SessionMeta[]): SessionGroupEntry[] {
  const groups = new Map<string, SessionGroupEntry>();
  for (const s of list) {
    const display = effectiveTitle(s);
    const key = dedupKey(display);
    const existing = groups.get(key);
    if (existing) {
      existing.sessions.push(s);
    } else {
      groups.set(key, {
        key,
        displayTitle: display,
        sessions: [s],
      });
    }
  }
  return Array.from(groups.values());
}
