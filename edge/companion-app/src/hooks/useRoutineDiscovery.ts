/** 发现周期性的事, 问要不要托管 (11/09 小鲶秘书 第 2 层)。规则在 lib/routines.ts。
 *
 * 一天最多跑一次: 10 点以后、Companion 开着的时候 (早上大家忙着开口, 等今天的请求也进来再看)。
 * 最近 8 周的请求不到 8 条就不看 (看不出规律, 白花一次模型调用)。
 */
import {
  fetchRoutineCandidates,
  routineReminder,
  ROUTINE_WINDOW_DAYS,
  usableRequests,
  validateCandidates,
} from "../lib/routines";
import { prefEnabled, ROUTINE_OFFER_KEY } from "../lib/secretaryPrefs";
import { cronJobsList, sessionsUserRequests } from "../lib/tauri";
import { useAgentStore } from "../store/agent";
import { useChatStore } from "../store/chat";
import { useRemindersStore } from "../store/reminders";

const LAST_RUN_KEY = "catfish:routine_discovery_day";
const START_HOUR = 10;
const MIN_REQUESTS = 8;

function today(now: Date): string {
  return `${now.getFullYear()}-${now.getMonth() + 1}-${now.getDate()}`;
}

function ranToday(now: Date): boolean {
  try {
    return localStorage.getItem(LAST_RUN_KEY) === today(now);
  } catch {
    return false;
  }
}

function markRan(now: Date): void {
  try {
    localStorage.setItem(LAST_RUN_KEY, today(now));
  } catch {
    /* 写不进: 最坏今天多看一次 */
  }
}

/** 一轮。返回挂上提醒条的条数。export 给测试。 */
export async function routineDiscoveryTick(now = new Date()): Promise<number> {
  if (!prefEnabled(ROUTINE_OFFER_KEY)) return 0;
  if (now.getHours() < START_HOUR || ranToday(now)) return 0;
  // 先标: 下面要调模型, 失败了今天也不再试 (明天再说), 不能每 15 分钟打一次
  markRan(now);
  const reqs = usableRequests(await sessionsUserRequests(ROUTINE_WINDOW_DAYS));
  if (reqs.length < MIN_REQUESTS) return 0;
  const jobs = await cronJobsList();
  const cands = await fetchRoutineCandidates(reqs, jobs, useChatStore.getState().model);
  if (!cands || cands.length === 0) return 0;
  const store = useRemindersStore.getState();
  const skip = store.activeKeys(now.getTime());
  const agentName = useAgentStore.getState().name || "小鲶";
  const offers = validateCandidates(cands, reqs, jobs, (k) => skip.has(k), agentName);
  let n = 0;
  for (const o of offers) {
    if (store.add(routineReminder(o, now, agentName), now.getTime())) n++;
  }
  console.log(`[routines] 候选 ${cands.length} 组, 核过节奏的 ${offers.length} 条, 挂上 ${n} 条`);
  return n;
}
