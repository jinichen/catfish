/** 主动定时调度 — BL-COMPANION-MORNING-PUSH (5/20).
 *
 * 每天 09:00 macOS 通知 push 早安播报 (邮件+日历+TODO 聚合 LLM 生成),
 * 员工没开 Companion 也能被拉回. 通知点击 → RunEvent::Reopen (lib.rs) 拉前台.
 *
 * 防抖: localStorage 记当天发没发, 同一时段不重复.
 *
 * 配置 (后续加 UI, 现在硬编码):
 *   localStorage["catfish:proactive_enabled"]      = "true" | "false" (默认 true)
 *   localStorage["catfish:morning_push_enabled"]   = "true" | "false" (默认 true)
 *
 * BL-PROACTIVE-STARTER-KILL (7/24 鸿波): 老 09:30 / 14:00 / 17:30 三个死时间的
 *   "proactive_starter" push (LLM 看 journal + 时段生成闲聊话题) 全砍. 原因跟
 *   Dashboard ProactiveCard 同 3 环 bug — gateway 只拿 journal_tail 有延迟 +
 *   memory sync_turn 5 轮/30 min 节流 vs 死时间不同步 + 无历史 dedupe. 员工
 *   反馈 "刚说过的事又弹". 05/25 BL-PROACTIVE-RUNAWAY 里说的 "4 死时间 + 5
 *   triggers = 9 条/天 spam" 里的 4 里, 09:00 早安留 (真信息), 另外 3 砍.
 *   桌宠气泡 (useProactiveTriggers silence/deadline/focus 事件驱动) 保留.
 */

import { useEffect } from "react";

import { fetchBriefingSuggestion, fetchMergedBriefing } from "../lib/briefing";

import {
  calendarWeekFetch,
  emailDigestFetch,
  remindersWeekFetch,
  petEmitBubble,
  petIsVisible,
  sendNotification,
  type CalendarEvent,
  type ReminderTodo,
} from "../lib/tauri";
import { useAgentStore } from "../store/agent";
import { useChatStore } from "../store/chat";
import { useUIStore } from "../store/ui";
import { isTauriRuntime } from "../lib/runtime";

interface ScheduledTime {
  time: string;                 // "HH:MM"
  source: "morning_briefing";
}

const TIMES_LOCAL: ScheduledTime[] = [
  { time: "09:00", source: "morning_briefing" },  // BL-COMPANION-MORNING-PUSH (5/20)
];
const _ENABLED_KEY = "catfish:proactive_enabled";
const _MORNING_PUSH_ENABLED_KEY = "catfish:morning_push_enabled";
const _LAST_FIRED_KEY = "catfish:proactive_last_fired";

function isEnabled(): boolean {
  try {
    const v = localStorage.getItem(_ENABLED_KEY);
    return v === null ? true : v === "true";
  } catch {
    return true;
  }
}

function isMorningPushEnabled(): boolean {
  try {
    const v = localStorage.getItem(_MORNING_PUSH_ENABLED_KEY);
    return v === null ? true : v === "true";
  } catch {
    return true;
  }
}

function todayKey(): string {
  const d = new Date();
  return `${d.getFullYear()}-${(d.getMonth() + 1)
    .toString()
    .padStart(2, "0")}-${d.getDate().toString().padStart(2, "0")}`;
}

function loadFiredMap(): Record<string, string[]> {
  try {
    const raw = localStorage.getItem(_LAST_FIRED_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function saveFiredMap(m: Record<string, string[]>): void {
  try {
    localStorage.setItem(_LAST_FIRED_KEY, JSON.stringify(m));
  } catch {
    /* ignore quota / etc */
  }
}

function markFired(time: string): void {
  const m = loadFiredMap();
  const key = todayKey();
  // 只保留今天 + 昨天 (清掉更老的)
  const today = key;
  const trimmed: Record<string, string[]> = {};
  for (const [k, v] of Object.entries(m)) {
    if (k === today) trimmed[k] = v;
  }
  trimmed[today] = [...(trimmed[today] || []), time];
  saveFiredMap(trimmed);
}

function nowHHMM(): string {
  const d = new Date();
  return `${d.getHours().toString().padStart(2, "0")}:${d
    .getMinutes()
    .toString()
    .padStart(2, "0")}`;
}

/** BL-COMPANION-MORNING-PUSH (5/20): 早安播报 starter 拉取.
 *
 * 并发拉邮件、日历和 Reminders 三源，喂 LLM 生成播报字符串。
 * LLM 挂 → rule-based fallback (跟 BriefingCard 同套路). 三源全空 → 返 null skip.
 */
async function fetchMorningBriefingStarter(): Promise<string | null> {
  const model = useChatStore.getState().model;
  const personality = useAgentStore.getState().personality;

  const [emailRes, calRes, todoRes] = await Promise.allSettled([
    emailDigestFetch(50),
    calendarWeekFetch(false),  // 本自然周（周一至周日）
    remindersWeekFetch(),
  ]);

  const unread = emailRes.status === "fulfilled"
    ? (() => { try { const a = JSON.parse(emailRes.value); return Array.isArray(a) ? a.length : 0; } catch { return 0; } })()
    : 0;
  const evts = calRes.status === "fulfilled"
    ? (() => { try { const a = JSON.parse(calRes.value); return Array.isArray(a) ? (a as CalendarEvent[]) : []; } catch { return []; } })()
    : [];
  const todos = todoRes.status === "fulfilled"
    ? (() => { try { const a = JSON.parse(todoRes.value); return Array.isArray(a) ? (a as ReminderTodo[]) : []; } catch { return []; } })()
    : [];

  // 三源都空 → 没必要 push
  if (unread === 0 && evts.length === 0 && todos.length === 0) {
    return null;
  }

  // BL-BRIEFING-LLM-MERGE (5/20): 试合并版 (1 调拿 todos + suggestion).
  // 桌宠 push 只用 suggestion, todos 部分丢掉 — 老路径 fallback 用单调用 suggestion.
  // BL-BRIEFING-LLM-PERSONALITY (5/20): 喂员工选的桌宠人格给 LLM
  const merged = await fetchMergedBriefing(unread, evts, todos, model, [], personality);
  if (merged) return merged.suggestion;

  // fallback 单调用 suggestion
  return await fetchBriefingSuggestion(unread, evts, todos, model, personality);
}

async function fireOne(time: string, source: ScheduledTime["source"]): Promise<void> {
  try {
    console.log(`[proactive] fireOne(${time}, ${source}): 拉 starter...`);

    // BL-PROACTIVE-STARTER-KILL (7/24): 现在只剩 morning_briefing 一支.
    // 老 proactive_starter 分支已砍 (跟 ProactiveCard 同 3 环 bug).
    if (!isMorningPushEnabled()) {
      console.log(`[proactive] morning_push disabled (localStorage), skip ${time}`);
      return;
    }
    let starter = await fetchMorningBriefingStarter();
    if (!starter) {
      console.log(`[proactive] fireOne(${time}, morning_briefing): 三源都空, skip`);
      return;
    }
    // 加个开头让员工知道这是"早安"而不是普通闲聊
    starter = `☀️ 早, ${starter}`;

    console.log(`[proactive] fireOne(${time}, ${source}): starter="${starter.slice(0, 60)}..."`);
    // 5/18 BL-COMPANION-VITE-CHUNK-WARN: useAgentStore 顶部 static (跟 App.tsx 等保持一致).
    const agentName = useAgentStore.getState().name || "小鲶";

    // 5/6 鸿波: 桌宠 = 主动信息统一出口, 不再砸 macOS 通知刷屏.
    //
    // 流程:
    //   1. 总是 prefill chat input (员工开 Companion 时一眼看到 starter, 改一下就发)
    //   2. 桌宠 visible → emit pet_bubble (桌宠头顶冒气泡, 桌宠 idle → thinking)
    //      桌宠 hidden  → 兜底 macOS 通知 (员工自己关了桌宠, 不能漏消息)
    useUIStore.getState().startProactiveChat(starter);

    let usedBubble = false;
    try {
      const visible = await petIsVisible();
      console.log(`[proactive] 桌宠 visible=${visible}`);
      if (visible) {
        // 5/6: emitTo frontend 跨窗目测不可靠, 走 Rust 命令 (app.emit_to) 100% 准
        const diag = await petEmitBubble(starter, agentName);
        console.log("[proactive] pet_emit_bubble Rust 返回诊断:", diag);
        usedBubble = true;
      }
    } catch (e) {
      console.warn("[proactive] pet_is_visible 失败, fallback macOS 通知:", e);
    }
    if (!usedBubble) {
      // BL-PROACTIVE-STARTER-KILL (7/24): 只剩 morning_briefing 分支, title 直接用.
      const title = `☀️ ${agentName}的早安播报`;
      console.log(`[proactive] 桌宠不可见, 走 macOS 通知 fallback (${title})`);
      await sendNotification(title, starter);
    }
    // 5/6 fix: markFired 已在 tick() 决定 fire 时立即标过, 这里不重复.
    console.log(`[proactive] fireOne(${time}, ${source}): ✅ 完成 (usedBubble=${usedBubble})`);
  } catch (e) {
    console.warn(`[proactive] fireOne(${time}, ${source}) 失败:`, e);
  }
}

/** 5/6 鸿波报"主动闲聊好像有问题": 老 tick 只在 cur === schedule 时刻的
 *  那一精确分钟触发. 员工 9:01 才打开 Companion → 9:00 那条整天丢. 这 =
 *  "早安播报看似没工作". 修成"过点补发":
 *
 *    遍历 TIMES_LOCAL, 找到 "今天还没发 + 当前时间 ≥ schedule time" 的最近一条 fire.
 *    一次只发一个 (防员工跨午夜启动一口气发 3 条).
 *    跨日 firedToday 自然清空 (todayKey 不同).
 *
 *  时间字符串比较: "HH:MM" lexicographic 顺序就是时间顺序, 直接 >= 即可.
 */
function _hhmmGE(a: string, b: string): boolean {
  return a >= b;
}

/** 每分钟看一次, 到点了就发. 简单 polling, 不用 cron. */
export function useProactiveScheduler(): void {
  useEffect(() => {
    if (!isTauriRuntime()) return;
    if (!isEnabled()) {
      console.log("[proactive] disabled (localStorage:catfish:proactive_enabled=false)");
      return;
    }

    const tick = () => {
      const cur = nowHHMM();
      const m = loadFiredMap();
      const today = todayKey();
      const firedToday = new Set(m[today] || []);

      // 找"今天该发但还没发"的最近一条
      // TIMES_LOCAL 升序, 反向遍历找最近过点的没发的
      for (let i = TIMES_LOCAL.length - 1; i >= 0; i--) {
        const { time: t, source } = TIMES_LOCAL[i];
        if (firedToday.has(t)) continue;
        if (_hhmmGE(cur, t)) {
          console.log(`[proactive] fire ${t} (source=${source}, now=${cur}, missed pickup)`);
          // 5/6 fix: markFired **立即**标 — 防 React StrictMode dev mode 双 mount
          // 让 useEffect 跑两次时, 第二次 tick 看到 firedToday 已含, 不重复 fire.
          // (老代码 markFired 在 fireOne 末尾, await 期间第二个 tick 抢着进来, LLM
          //  调两次 + 员工双气泡/双通知 spam)
          markFired(t);
          void fireOne(t, source);
          return; // 一次只发一个
        }
      }
    };

    console.log(
      `[proactive] scheduler 启动, times=${TIMES_LOCAL.map((t) => `${t.time}/${t.source}`).join(",")} (mount @ ${nowHHMM()})`,
    );
    // mount 立即看一下 (员工任何时间打开都能补发当天最近过点的那条)
    tick();
    const t = window.setInterval(tick, 60_000);  // 每分钟看一次
    return () => window.clearInterval(t);
  }, []);
}
