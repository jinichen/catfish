/** 技能 / 工作流的中文名 (10/19) —— Rust commands/skill_names.rs 的前端封装, 规则在 tool-bridge skill_names.py。 */
import { invoke } from "@tauri-apps/api/core";

/** 改名 (只能改你自己电脑上的技能; 团队审定的、装来的专家包不能改) */
export const skillNameSet = (path: string, title: string) =>
  invoke<{ ok: boolean; title: string }>("skill_name_call", { action: "set", params: { path, title } });

/** 一次性: 让小鲶给还没像样中文名的技能起名 (试过的不再试)。返回 {named: {目录: 新名字}} */
export const skillNamesBackfill = () =>
  invoke<{ ok: boolean; named: Record<string, string>; error?: string }>("skill_name_call", { action: "backfill" });

let backfillOnce: Promise<boolean> | null = null;
/** 每次打开 Companion 最多跑一次; 有新名字返回 true (调用方刷新列表) */
export function backfillNamesOnce(): Promise<boolean> {
  backfillOnce ??= skillNamesBackfill()
    .then((r) => Object.keys(r.named ?? {}).length > 0)
    .catch(() => false);
  return backfillOnce;
}
