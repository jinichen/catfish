/** 早安卡片「已关闭」台账 (10/8) —— `~/.catfish/advisor_closed.jsonl`。
 *
 * # 为什么 (10/8 鸿波「这个不是已经完成填写了吗? 为什么又出来」)
 *
 * 关闭状态原来只存在 advisor_cache 的 taskChatSummaries[uid].manualStatus。而每次写
 * 缓存只保留本轮结果里还在的 uid (briefing_advisor.ts / AdvisorView.tsx 两处保存) ——
 * 被判已完成的卡正是本轮被拿掉的那张, 它的状态当场一起被删。下一轮没人记得它关过,
 * 来源邮件还在本周窗口里就又生成一张。同一封邮件线上的催办 (【提醒】转发原通知)
 * 换个 uid、换个标题, 更是直接绕过只按 uid/标题精确匹配的 filterResolvedTasks。
 *
 * 这份台账不随缓存重写清理, 按三把钥匙匹配:
 *   · uid           同一张卡
 *   · 规范化标题      去掉括号内容和标点后相等, 或一方包含另一方 (≥8 字)
 *   · 来源邮件主题    contextRefs 里的邮件主题去掉【提醒】/回复:/转发: 等前缀后相等
 *                    —— 催办、回复、转发都落在同一条线上, 关一次全线关
 *
 * 写入方: 卡片按钮 (setTaskManualStatus) 和小鲶的 catfish_advisor_close_task
 * (tool-bridge)。读侧折叠: 同一钥匙的最新一行算数, reopen 撤销 close。
 */
import { invoke as rawInvoke } from "@tauri-apps/api/core";
import type { AdvisorResult, HandledSilentlyItem, MainTask } from "./briefing_advisor_common";

export type ClosedStatus = "done" | "ignored" | "snoozed";

export interface ClosedLedgerEntry {
  op: "close" | "reopen";
  status?: ClosedStatus;
  taskUid?: string;
  title?: string;
  /** 关闭时卡片的 contextRefs 原文, 读侧从里面取邮件主题 */
  refs?: string[];
  ts: string;
  /** snoozed 的截止时刻 (次日 0 点); 过了就不再算关闭 */
  until?: string;
  by?: "button" | "chat";
  note?: string;
}

/** done / ignored 关闭后多久失效 —— 来源窗口是一周, 30 天足够盖住同一件事的所有催办 */
export const CLOSED_TTL_DAYS = 30;

export const advisorClosedRead = () =>
  rawInvoke<ClosedLedgerEntry[]>("advisor_closed_read");

export const advisorClosedAppend = (entry: ClosedLedgerEntry) =>
  rawInvoke<void>("advisor_closed_append", { entry });

/** 下一个本地 0 点 (snoozed = 今天不出, 明天照常) */
export function nextLocalMidnight(now: Date = new Date()): string {
  const d = new Date(now);
  d.setHours(24, 0, 0, 0);
  return d.toISOString();
}

// ── 规范化 ──────────────────────────────────────────────────

const BRACKETS = /【[^】]*】|\[[^\]]*\]|（[^）]*）|\([^)]*\)/g;
const PUNCT = /[\s\p{P}\p{S}]/gu;

/** 标题: 去括号内容 (【提醒】/（含无形资产）这类修饰) + 标点空白, 小写 */
export function normTitle(title: string): string {
  return title.replace(BRACKETS, "").replace(PUNCT, "").toLowerCase();
}

const REPLY_PREFIX = /^(?:re|fw|fwd|回复|答复|转发)\s*[:：]\s*/i;

/** 邮件主题: 反复剥掉前缀标签 (【提醒】【整改工作要求】) 和 回复:/转发:/Re: */
export function normSubject(subject: string): string {
  let s = subject.trim();
  for (let i = 0; i < 10; i += 1) {
    const before = s;
    s = s.replace(/^(?:【[^】]*】|\[[^\]]*\])\s*/, "").replace(REPLY_PREFIX, "").trim();
    if (s === before) break;
  }
  return normTitle(s);
}

/** contextRefs 一条 → 邮件主题 (不是邮件引用返 "")。
 *  形如「邮件：<主题>（邮件 ID: …；时间: …）」, 主题里本身可能有全角括号, 所以从
 *  「（邮件 ID」/「(邮件 ID」往前截, 不按第一个括号截。 */
export function subjectFromRef(ref: string): string {
  const m = ref.match(/^\s*邮件\s*[:：]\s*(.*)$/s);
  if (!m) return "";
  const body = m[1];
  const cut = body.search(/[（(]\s*邮件\s*ID/i);
  return (cut >= 0 ? body.slice(0, cut) : body).trim();
}

function subjectKeys(refs: string[] | undefined): string[] {
  const keys = new Set<string>();
  for (const r of refs ?? []) {
    const k = normSubject(subjectFromRef(r));
    // 太短的主题 (「通知」「回复」) 不当钥匙, 会误伤
    if (k.length >= 8) keys.add(k);
  }
  return [...keys];
}

// ── 折叠 + 匹配 ───────────────────────────────────────────────

export interface ActiveClosure {
  status: ClosedStatus;
  by?: ClosedLedgerEntry["by"];
  taskUid?: string;
  title?: string;
  titleKey: string;
  subjectKeys: string[];
  ts: string;
}

function closureKey(e: { taskUid?: string; title?: string }): string {
  return e.taskUid ? `uid:${e.taskUid}` : `title:${normTitle(e.title ?? "")}`;
}

/** 按时间顺序折叠: 同一张卡 (uid, 没 uid 退到规范化标题) 最新一行算数; 过期的不算 */
export function foldLedger(entries: ClosedLedgerEntry[], now: Date = new Date()): ActiveClosure[] {
  const latest = new Map<string, ClosedLedgerEntry>();
  const sorted = [...entries].sort((a, b) => (a.ts ?? "").localeCompare(b.ts ?? ""));
  for (const e of sorted) {
    if (!e || (e.op !== "close" && e.op !== "reopen")) continue;
    if (!e.taskUid && !e.title) continue;
    if (e.op === "reopen") {
      // reopen 按 uid 或标题撤 —— 员工点「撤销」时卡片 uid 可能已经换过
      const rTitle = e.title ? normTitle(e.title) : "";
      for (const [k, v] of latest) {
        if ((e.taskUid && v.taskUid === e.taskUid) || (rTitle && v.title && normTitle(v.title) === rTitle)) {
          latest.delete(k);
        }
      }
      continue;
    }
    latest.set(closureKey(e), e);
  }
  const ttlMs = CLOSED_TTL_DAYS * 86400_000;
  const out: ActiveClosure[] = [];
  for (const e of latest.values()) {
    const status = e.status ?? "done";
    const ts = Date.parse(e.ts);
    if (status === "snoozed") {
      if (!e.until || Date.parse(e.until) <= now.getTime()) continue;
    } else if (!Number.isFinite(ts) || now.getTime() - ts > ttlMs) {
      continue;
    }
    out.push({
      status,
      by: e.by,
      taskUid: e.taskUid,
      title: e.title,
      titleKey: normTitle(e.title ?? ""),
      subjectKeys: subjectKeys(e.refs),
      ts: e.ts,
    });
  }
  return out;
}

export type ClosedMatch = { closure: ActiveClosure; by: "uid" | "title" | "subject" };

export function matchClosed(task: MainTask, closures: ActiveClosure[]): ClosedMatch | null {
  const t = normTitle(task.title);
  const subs = subjectKeys(task.contextRefs);
  for (const c of closures) {
    if (c.taskUid && c.taskUid === task.taskUid) return { closure: c, by: "uid" };
  }
  for (const c of closures) {
    if (!c.titleKey || !t) continue;
    const [a, b] = c.titleKey.length <= t.length ? [c.titleKey, t] : [t, c.titleKey];
    if (a === b || (a.length >= 8 && b.includes(a))) return { closure: c, by: "title" };
  }
  for (const c of closures) {
    if (c.subjectKeys.some((k) => subs.includes(k))) return { closure: c, by: "subject" };
  }
  return null;
}

/** 生成结果后过滤: 命中台账的卡挪进「已处理」折叠区, 跟 filterResolvedTasks 同款去向 */
export function filterClosedTasks(result: AdvisorResult, closures: ActiveClosure[]): AdvisorResult {
  if (closures.length === 0) return result;
  const kept: MainTask[] = [];
  const handled: HandledSilentlyItem[] = [];
  for (const task of result.mainTasks) {
    const m = matchClosed(task, closures);
    if (!m) {
      kept.push(task);
      continue;
    }
    console.log(
      `[advisor closed-ledger] drop ${task.taskUid} "${task.title}" ` +
        `(status=${m.closure.status}, matchedBy=${m.by}, closedAt=${m.closure.ts})`,
    );
    handled.push({
      type: "task_resolved",
      count: 1,
      category: `${task.title} 已标记${m.closure.status === "snoozed" ? "今天先不看" : "完成"} (${m.closure.ts.slice(0, 10)})`,
    });
  }
  if (handled.length === 0) return result;
  return {
    ...result,
    mainTasks: kept.map((t, i) => ({ ...t, id: i + 1 })),
    handledSilently: [...result.handledSilently, ...handled],
  };
}

/** 读台账失败 (老版本后端 / 文件坏) 一律当没有关闭记录, 不拦早安 */
export async function loadActiveClosures(now: Date = new Date()): Promise<ActiveClosure[]> {
  try {
    return foldLedger(await advisorClosedRead(), now);
  } catch (e) {
    console.warn("[advisor closed-ledger] 读台账失败 (按无关闭记录处理):", e);
    return [];
  }
}
