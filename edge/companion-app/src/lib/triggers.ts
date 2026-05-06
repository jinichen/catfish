/** BL-E13.5 真主动 Phase A — 信号检测纯函数.
 *
 * 5/6 鸿波 "现在那个不好玩, 直接做" 后启动. 跟现有 useProactiveScheduler
 * (9:30/14:00/17:30 死时间) 并存, 后者保留作兜底.
 *
 * 设计:
 *   - 全部纯函数 (无 side-effect, 易测试)
 *   - 输入: state snapshot (chat messages / journal text / 时间戳 / fired log)
 *   - 输出: { kind: 'silence' | 'deadline' | 'focus' | null, message: string, why: string }
 *   - useProactiveTriggers 1 分钟 tick, 调这堆函数, 第一个非 null 信号触发气泡
 *
 * 不打扰策略 (全在这里实现, hook 只调度):
 *   - 时段 22:00-7:00 不主动
 *   - 今天已主动 ≥ MAX_PROACTIVE_PER_DAY 次封顶
 *   - 最近 SNOOZE_AFTER_DISMISS_MS 内员工 dismiss 过气泡 → 静默
 *   - 同 signal kind 最近 SAME_KIND_COOLDOWN_MS 内不重复
 */

import type { ChatMessage } from "../types/chat";

export type TriggerKind = "silence" | "deadline" | "focus";

export interface TriggerResult {
  kind: TriggerKind;
  message: string; // 本地模板拼的话 (LLM 不可用时兜底)
  why: string; // 给开发 console / audit 看的触发原因
  context: Record<string, unknown>; // 5/6 Phase B: 给 gateway LLM 重写 starter 用的结构化 context
}

/** 不打扰: 一天最多主动几次 */
export const MAX_PROACTIVE_PER_DAY = 5;
/** dismiss 后多久内静默 */
export const SNOOZE_AFTER_DISMISS_MS = 30 * 60_000;
/** 同 kind 触发后多久内不重复 */
export const SAME_KIND_COOLDOWN_MS = 30 * 60_000;
/** chat 沉默触发最低门槛 */
export const SILENCE_THRESHOLD_MS = 30 * 60_000;
/** focus 切回 Companion 触发最低门槛 (距上次活跃) */
export const FOCUS_RETURN_MIN_AWAY_MS = 30 * 60_000;

/** 工作时段 (本地小时) */
export const QUIET_HOURS_START = 22;
export const QUIET_HOURS_END = 7;

interface FiredLogEntry {
  kind: TriggerKind;
  ts: number;
}

/** 时段 / 频率 / dismiss / cooldown 4 重不打扰. 返 true = 该静默. */
export function shouldStaySilent(args: {
  now: Date;
  firedLog: FiredLogEntry[];
  lastDismissTs: number | null;
  candidateKind: TriggerKind;
}): { silent: boolean; reason: string } {
  const { now, firedLog, lastDismissTs, candidateKind } = args;
  const hour = now.getHours();
  if (hour >= QUIET_HOURS_START || hour < QUIET_HOURS_END) {
    return { silent: true, reason: `quiet_hours (${hour}:xx)` };
  }
  // 今天 fired
  const today = todayKey(now);
  const todayFired = firedLog.filter((e) => todayKey(new Date(e.ts)) === today);
  if (todayFired.length >= MAX_PROACTIVE_PER_DAY) {
    return { silent: true, reason: `daily_cap (${todayFired.length})` };
  }
  // dismiss snooze
  if (lastDismissTs && now.getTime() - lastDismissTs < SNOOZE_AFTER_DISMISS_MS) {
    return { silent: true, reason: "recent_dismiss" };
  }
  // same-kind cooldown
  const sameKindRecent = firedLog
    .filter((e) => e.kind === candidateKind)
    .sort((a, b) => b.ts - a.ts)[0];
  if (
    sameKindRecent &&
    now.getTime() - sameKindRecent.ts < SAME_KIND_COOLDOWN_MS
  ) {
    return { silent: true, reason: `same_kind_cooldown (${candidateKind})` };
  }
  return { silent: false, reason: "" };
}

function todayKey(d: Date): string {
  return `${d.getFullYear()}-${(d.getMonth() + 1)
    .toString()
    .padStart(2, "0")}-${d.getDate().toString().padStart(2, "0")}`;
}

// ── 信号 1: chat 沉默 ──────────────────────────────────────────

/** 末尾消息含动词关键词 (跟员工正在做某事) */
const ACTION_KEYWORDS = [
  "去", "弄", "做", "搞", "写", "整", "看",
  "找", "查", "约", "开", "联系", "等",
  "处理", "跟进", "改", "提交", "上报",
  "材料", "方案", "审", "签",
];

export function detectSilence(args: {
  messages: ChatMessage[];
  now: Date;
}): TriggerResult | null {
  const { messages, now } = args;
  if (messages.length === 0) return null;
  const last = messages[messages.length - 1];
  if (last.role !== "user") return null;
  // 末尾必须是 user msg (说明员工说了一句话, 鲶鱼回了, 然后沉默)
  // 但实际上可能是 ...user msg → assistant msg → 沉默. 我们要的是"鲶鱼回完后, 员工又沉默 30 min".
  // 改成: 找最后一条 user msg 的 ts, 跟 now 比.
  const lastUser = [...messages].reverse().find((m) => m.role === "user");
  if (!lastUser) return null;
  const lastUserTs = Date.parse(lastUser.ts);
  if (Number.isNaN(lastUserTs)) return null;
  const elapsed = now.getTime() - lastUserTs;
  if (elapsed < SILENCE_THRESHOLD_MS) return null;

  const text = lastUser.content || "";
  const hits = ACTION_KEYWORDS.filter((kw) => text.includes(kw));
  if (hits.length === 0) return null;

  // 抽出员工在做的事 — 简单取末句或前 40 字
  const snippet = text.length > 40 ? text.slice(0, 40) + "..." : text;
  const minsAgo = Math.round(elapsed / 60000);

  return {
    kind: "silence",
    message: `${minsAgo} 分钟没动静了。你刚说"${snippet}", 现在还在弄吗? 卡哪我帮看看.`,
    why: `silence ${minsAgo}min, last_user 含 [${hits.join(",")}]`,
    context: {
      minutes_ago: minsAgo,
      last_user_text: snippet,
      action_hits: hits.join("/"),
    },
  };
}

// ── 信号 2: journal deadline ──────────────────────────────────

/** 抽 journal 里 "5/15"/"5月15日"/"下周三"/"明天" 等日期关键词, 对照当前日期 */
export function detectDeadline(args: {
  journalText: string;
  now: Date;
}): TriggerResult | null {
  const { journalText, now } = args;
  if (!journalText) return null;

  // 取最近 30 天的 journal 内容 (太老的 deadline 没意义)
  // 简单切: 拿后 50KB
  const recent = journalText.length > 50_000
    ? journalText.slice(-50_000)
    : journalText;

  // 模式 1: "5/15", "5/14", "5月15日" 等具体日期
  const dateMatches = [...recent.matchAll(/(\d{1,2})[\/月](\d{1,2})日?/g)];
  for (const m of dateMatches) {
    const month = parseInt(m[1], 10);
    const day = parseInt(m[2], 10);
    if (month < 1 || month > 12 || day < 1 || day > 31) continue;
    // 假设今年的这个日期. 若已过今年, 跳过 (太老)
    const year = now.getFullYear();
    const target = new Date(year, month - 1, day);
    const daysUntil = Math.floor(
      (target.getTime() - now.getTime()) / 86400_000,
    );
    // 触发: 3 天内的 deadline (含今天/明天/后天)
    if (daysUntil >= 0 && daysUntil <= 3) {
      // 拉出 deadline 周围的上下文 (前后 60 字)
      const idx = m.index ?? 0;
      const ctx = recent
        .slice(Math.max(0, idx - 60), Math.min(recent.length, idx + 60))
        .replace(/\s+/g, " ")
        .trim();
      const dayLabel =
        daysUntil === 0 ? "今天" : daysUntil === 1 ? "明天" : `${daysUntil} 天后`;
      return {
        kind: "deadline",
        message: `${dayLabel}是 ${month}/${day} — 你 journal 里提过这个: "${ctx.slice(0, 80)}". 还差啥, 我帮你?`,
        why: `deadline ${month}/${day} in ${daysUntil}d`,
        context: {
          days_until: daysUntil,
          date_str: `${month}/${day}`,
          journal_excerpt: ctx.slice(0, 100),
        },
      };
    }
  }

  return null;
}

// ── 信号 3: focus 切回 Companion ───────────────────────────────

/**
 * 员工 ≥ 30 min 前切走 Companion, 现在切回来.
 * lastFocusLeftTs / lastFocusReturnTs 在 useProactiveTriggers 里 useRef 维护.
 */
export function detectFocusReturn(args: {
  lastFocusLeftTs: number | null;
  lastFocusReturnTs: number | null;
  now: Date;
  messages: ChatMessage[];
}): TriggerResult | null {
  const { lastFocusLeftTs, lastFocusReturnTs, now, messages } = args;
  if (!lastFocusLeftTs || !lastFocusReturnTs) return null;
  // 离开时长 ≥ 30 min
  const awayMs = lastFocusReturnTs - lastFocusLeftTs;
  if (awayMs < FOCUS_RETURN_MIN_AWAY_MS) return null;
  // return 必须刚刚发生 (过去 90 秒内)
  if (now.getTime() - lastFocusReturnTs > 90_000) return null;

  // 拿走前最后一条 user msg
  const lastUser = [...messages].reverse().find((m) => m.role === "user");
  if (!lastUser) return null;
  const lastUserTs = Date.parse(lastUser.ts);
  if (Number.isNaN(lastUserTs) || lastUserTs > lastFocusLeftTs) return null;

  const snippet = (lastUser.content || "").slice(0, 40);
  const minsAway = Math.round(awayMs / 60000);
  return {
    kind: "focus",
    message: `回来啦, ${minsAway} 分钟没见。刚才你说"${snippet}", 还在等我跟进吗?`,
    why: `focus_return after ${minsAway}min, lastUser=${snippet.slice(0, 20)}`,
    context: {
      minutes_away: minsAway,
      last_user_text: snippet,
    },
  };
}

// ── 总入口: 按优先级返第一个命中的 trigger ────────────────────

export function detectAnyTrigger(args: {
  now: Date;
  messages: ChatMessage[];
  journalText: string;
  lastFocusLeftTs: number | null;
  lastFocusReturnTs: number | null;
  firedLog: FiredLogEntry[];
  lastDismissTs: number | null;
}): TriggerResult | null {
  // 优先级: deadline > silence > focus
  // (deadline 时效性最强, focus 偶发性最强但场景最具体)
  const candidates = [
    detectDeadline({ journalText: args.journalText, now: args.now }),
    detectSilence({ messages: args.messages, now: args.now }),
    detectFocusReturn({
      lastFocusLeftTs: args.lastFocusLeftTs,
      lastFocusReturnTs: args.lastFocusReturnTs,
      now: args.now,
      messages: args.messages,
    }),
  ];
  for (const c of candidates) {
    if (!c) continue;
    const guard = shouldStaySilent({
      now: args.now,
      firedLog: args.firedLog,
      lastDismissTs: args.lastDismissTs,
      candidateKind: c.kind,
    });
    if (guard.silent) {
      console.log(`[triggers] ${c.kind} 命中但被静默: ${guard.reason}`);
      continue;
    }
    return c;
  }
  return null;
}
