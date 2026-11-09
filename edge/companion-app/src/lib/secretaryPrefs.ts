/** 小鲶秘书三个开关 (11/09), 默认都开。仪表盘「名字 + 风格」卡上能关 (AgentPrefsCard)。
 *   · 发现周期性的事, 问要不要托管 (hooks/useRoutineDiscovery.ts)
 *   · 提前准备: 会议、出差拜访、快到期的待办、邮件里要办 / 要回的事 (hooks/useAdvancePrep.ts; key 沿用第一版的会前准备)
 *   · 群里 @我 交代的事挂到提醒条 (lib/roomAskFlow.ts)
 * 「定时任务做完就报」不设开关: 那是员工自己建的任务, 结果本来就该告诉他。 */

export const ROUTINE_OFFER_KEY = "catfish:routine_offer_enabled";
export const MEETING_PREP_KEY = "catfish:meeting_prep_enabled";
/** 11/09 群里 @我 交代的事 → 提醒条 (lib/roomAskFlow.ts) */
export const ROOM_ASK_KEY = "catfish:room_ask_enabled";

export function prefEnabled(key: string): boolean {
  try {
    return localStorage.getItem(key) !== "false";
  } catch {
    return true;
  }
}

export function setPref(key: string, on: boolean): void {
  try {
    localStorage.setItem(key, on ? "true" : "false");
  } catch {
    /* 写不进: 这次进程里还是原值 */
  }
}
