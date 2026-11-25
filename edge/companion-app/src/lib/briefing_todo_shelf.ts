/**
 * 早安出卡前整理任务清单 (11/25 鸿波「早安里很多重复的条目和很久之前的数据」)。
 *
 * 早安把任务库里所有未完成任务都喂给模型出卡:
 *  - 没有截止日期、又很久没动过的 (会议行动项、几个月前导进来的总条目) 每天都占一张卡
 *    → 收到「搁置很久」一组, 不进主列表, 也不喂给模型。
 *  - 同一件事挂了几条 (主线 + 某个节点: 「资质集采三期：…11/30…」「资质集采三期：12/01…」)
 *    → 喂给模型时并成一组, 明说只出一张卡。
 */
import type { HandledSilentlyItem } from "./briefing_advisor_common";
import type { ReminderTodo } from "./tauri_briefing";

/** 没有截止日期、超过这么多天没动 = 搁置 */
export const SHELF_DAYS = 14;
export const SHELVED_TYPE = "task_shelved";

export function shelveStaleTodos(
  todos: ReminderTodo[],
  nowMs: number = Date.now(),
): { active: ReminderTodo[]; shelved: HandledSilentlyItem[] } {
  const active: ReminderTodo[] = [];
  const shelved: HandledSilentlyItem[] = [];
  for (const t of todos) {
    const days = typeof t.updated_at === "number" && t.updated_at > 0
      ? Math.floor((nowMs - t.updated_at * 1000) / 86_400_000)
      : 0;
    // 有截止日期的、标了重点的, 再久也留在主列表 (到期提醒不能丢)
    if (!t.due_date_iso && !t.is_priority && days >= SHELF_DAYS) {
      shelved.push({ type: SHELVED_TYPE, count: 0, category: `${t.text}（${days} 天没动，没有截止日期）` });
    } else {
      active.push(t);
    }
  }
  return { active, shelved };
}

/** 「资质集采三期：两包均已定…」→「资质集采三期」。冒号前至少 4 个字才算一件事的名字。 */
export function matterKey(title: string): string | null {
  const m = /^[⭐\s]*([^：:]{4,24})[：:]/.exec(title);
  // \W 会把中文也当成「非单词」删掉, 所以明写: 空白 / 标点 / 符号
  const key = m ? m[1].replace(/[\s\p{P}\p{S}_]+/gu, "").toLowerCase() : "";
  return key.length >= 4 ? key : null;
}

/** 按「同一件事」分组, 保持原顺序 (组的位置 = 组里第一条的位置)。 */
export function groupSameMatter<T extends { text: string }>(todos: T[]): T[][] {
  const groups: T[][] = [];
  const byKey = new Map<string, T[]>();
  for (const t of todos) {
    const key = matterKey(t.text);
    const hit = key ? byKey.get(key) : undefined;
    if (hit) {
      hit.push(t);
      continue;
    }
    const group = [t];
    groups.push(group);
    if (key) byKey.set(key, group);
  }
  return groups;
}
