/** 让小鲶改纪要 (11/17) 的纯函数: 改前改后对比、建议的说法。界面在 MinutesRevise.tsx (文件名不能叫 minutesRevise.ts: macOS 不分大小写, 跟它撞名)。 */
import type { ActionItem, MinutesJson } from "../../lib/tauri_meeting";

export type DiffKind = "same" | "added" | "removed" | "changed";
/** changed: 同一条 (待办事项没变), 负责人 / 截止改了; old 是改前那条 */
export interface DiffRow<T> { value: T; kind: DiffKind; old?: T }

/** 按 LCS 对齐两组 (条目 / 行), 保留顺序: 删掉的排在它原来的位置, 新加的排在新位置。 */
export function diffList<T>(before: T[], after: T[], key: (x: T) => string = (x) => String(x)): DiffRow<T>[] {
  const a = before.map(key);
  const b = after.map(key);
  const n = a.length;
  const m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out: DiffRow<T>[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ value: after[j], kind: "same" });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ value: before[i++], kind: "removed" });
    } else {
      out.push({ value: after[j++], kind: "added" });
    }
  }
  while (i < n) out.push({ value: before[i++], kind: "removed" });
  while (j < m) out.push({ value: after[j++], kind: "added" });
  return out;
}

export const itemKey = (it: ActionItem) => `${it.task.trim()}|${it.owner.trim()}|${it.due}`;

/** 待办按事项对齐: 事项一样、只改了负责人 / 截止的算「改了」(一行, 不是划掉一整条再加一整条)。 */
export function diffItems(before: ActionItem[], after: ActionItem[]): DiffRow<ActionItem>[] {
  return diffList(before, after, (it) => it.task.trim()).map((r) => {
    if (r.kind !== "same") return r;
    const old = before.find((b) => b.task.trim() === r.value.task.trim());
    return old && itemKey(old) !== itemKey(r.value) ? { ...r, kind: "changed", old } : r;
  });
}

export function itemText(it: ActionItem): string {
  const extra = [it.owner, it.due && `截止 ${it.due}`].filter(Boolean).join(" · ");
  return extra ? `${it.task} (${extra})` : it.task;
}

/** 全文 (按文字模版写的那份) 的逐行对比, 只留改动的地方和前后各一行 (其余折成 「…」)。 */
export function markdownHunks(before: string, after: string, context = 1): (DiffRow<string> | null)[] {
  const rows = diffList(before.trimEnd().split("\n"), after.trimEnd().split("\n"));
  const keep = rows.map((r) => r.kind !== "same");
  const show = rows.map((_, i) => {
    for (let k = Math.max(0, i - context); k <= Math.min(rows.length - 1, i + context); k++) if (keep[k]) return true;
    return false;
  });
  const out: (DiffRow<string> | null)[] = [];
  rows.forEach((r, i) => {
    if (show[i]) out.push(r);
    else if (out.length && out[out.length - 1] !== null) out.push(null);
  });
  while (out.length && out[out.length - 1] === null) out.pop();
  return out;
}

/** 给员工的几个现成说法 (点了填进输入框, 不直接发)。 */
export function reviseSuggestions(m: MinutesJson, markdown: string, names: Record<string, string>): string[] {
  const out: string[] = [];
  const text = `${markdown}\n${m.action_items.map((x) => x.owner).join(" ")}`;
  const named = Object.entries(names).filter(([, v]) => v.trim());
  if (named.length && /说话人\d+/.test(text)) {
    const pairs = named.slice(0, 4).map(([k, v]) => `说话人${Number(k) + 1}=${v.trim()}`).join("、");
    out.push(`把纪要里的说话人编号换成真名 (${pairs}${named.length > 4 ? " 等, 见说话人名单" : ""})`);
  } else if (/说话人\d+/.test(text)) {
    out.push("说话人3 是 ×××, 纪要和待办负责人里都换成真名");
  }
  out.push("摘要压短到三句话, 只留结论");
  out.push("对照转写检查有没有漏掉的待办和负责人");
  return out;
}
