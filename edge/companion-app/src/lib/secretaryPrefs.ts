/** 小鲶秘书两个开关 (11/09), 默认都开。仪表盘「名字 + 风格」卡上能关 (AgentPrefsCard)。
 *   · 发现周期性的事, 问要不要托管 (hooks/useRoutineDiscovery.ts)
 *   · 会前自动准备会务安排 (hooks/useMeetingPrep.ts)
 * 「定时任务做完就报」不设开关: 那是员工自己建的任务, 结果本来就该告诉他。 */

export const ROUTINE_OFFER_KEY = "catfish:routine_offer_enabled";
export const MEETING_PREP_KEY = "catfish:meeting_prep_enabled";

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
