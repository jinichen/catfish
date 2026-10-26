/** 等员工批准的那几步 —— 全局一份 (10/26)。
 *
 * 原来每个横幅 (工作台 ChatPanel / 早安卡片) 各自用 useState 记**一条** pending:
 *   - 两段对话同时等批准, 后来的盖掉先来的; 先来的在 hermes 里还等着, 界面上没了, 到点悄悄作废
 *   - 横幅不跟会话走: 切到别的对话, 横幅还挂着, 看着像这段对话要批
 *   - 工作台只在当前标签页时挂载: 人在别的页面时来的请求, 切回去也看不到 (早安卡片一直挂着但隐藏)
 *   - 两个横幅都挂着时各弹一次系统通知; 一边批了另一边不知道, 到点还报「已自动拒绝」
 *   - 「批准并继续」发进**当前打开**的对话, 切过会话就发错地方
 *
 * 现在: chat.ts 派发的 `catfish:approval-pending` 全进这里 (带 session_id), 每条有自己的截止时间;
 * 界面按会话取自己那份显示, 别的对话的只提一句「去看看」。
 */
import { create } from "zustand";

import {
  approvalErrorMessage, approvalNotification, approvalTimeoutNote, matchesPreapproval,
  retryAfterTimeoutPrompt, RETRY_PREAPPROVAL_MS, shouldNotifyApproval,
} from "../lib/approvalOutcome";
import { currentApprovalTimeoutS, setApprovalTimeoutS, type PendingApprovalDetail } from "../lib/nativeApprovals";
import { hermesApprovalTimeout, sendNotification, toolBridgeChatApproval } from "../lib/tauri";

export type ApprovalChoice = "once" | "session" | "always" | "deny";

export interface ApprovalItem {
  key: string;
  sessionId: string | null;
  command: string;
  description: string;
  patternKey: string;
  timeoutS: number;
  deadline: number;
}

/** 留在界面上的一句话 (超时作废 / 批准没送出去)。command 非空 = 可以「批准并继续」。 */
export interface ApprovalNote {
  id: string;
  sessionId: string | null;
  text: string;
  command: string | null;
}

interface SessionPreapproval {
  sessionId: string | null;
  command: string;
  until: number;
}

export interface ApprovalsState {
  items: ApprovalItem[];
  notes: ApprovalNote[];
  submitting: Record<string, ApprovalChoice>;
  preapprovals: SessionPreapproval[];
}

const empty: ApprovalsState = { items: [], notes: [], submitting: {}, preapprovals: [] };

export const useApprovalsStore = create<ApprovalsState>(() => ({ ...empty }));

/** 这条界面该不该显: 同一段对话, 或者请求本身不知道是哪段 (老插件那套)。 */
export function belongsTo(sessionId: string | null, current: string | null): boolean {
  return sessionId === null || sessionId === current;
}

// ── 纯函数 (单测直接测) ──────────────────────────────

export function itemFromDetail(d: PendingApprovalDetail, now: number): ApprovalItem {
  const timeoutS = d.timeout_s ?? currentApprovalTimeoutS();
  return {
    key: d.approval_session_key,
    sessionId: d.session_id ?? null,
    command: d.command ?? "",
    description: d.description ?? "",
    patternKey: d.pattern_key ?? "",
    timeoutS,
    deadline: now + timeoutS * 1000,
  };
}

/** 来了一条: 同 key 的替换 (hermes 重发), 别的照留 —— 不再互相盖掉。
 *  这段对话刚点过「批准并继续」且命令一样 → 返回 preapproved, 调用方直接批。 */
export function addItem(s: ApprovalsState, item: ApprovalItem, now: number): { state: ApprovalsState; preapproved: boolean } {
  const live = s.preapprovals.filter((p) => now <= p.until);
  const hit = live.find((p) => p.sessionId === item.sessionId && matchesPreapproval(p, item.command, now));
  if (hit) {
    return { state: { ...s, preapprovals: live.filter((p) => p !== hit) }, preapproved: true };
  }
  return {
    state: {
      ...s,
      preapprovals: live,
      items: [...s.items.filter((i) => i.key !== item.key), item],
      // 这段对话又在要批准了, 之前留的那句「作废了」就不用再挂着
      notes: s.notes.filter((n) => n.sessionId !== item.sessionId || n.command === null),
    },
    preapproved: false,
  };
}

/** 到点的转成「作废了」提示 (带命令, 可以批准并继续)。 */
export function expireItems(s: ApprovalsState, now: number): ApprovalsState {
  const due = s.items.filter((i) => now >= i.deadline);
  if (due.length === 0) return s;
  return {
    ...s,
    items: s.items.filter((i) => now < i.deadline),
    notes: [
      ...s.notes,
      ...due.map((i) => ({ id: `timeout:${i.key}`, sessionId: i.sessionId, text: approvalTimeoutNote(i.timeoutS), command: i.command })),
    ],
  };
}

export function removeItem(s: ApprovalsState, key: string): ApprovalsState {
  const { [key]: _drop, ...submitting } = s.submitting;
  return { ...s, items: s.items.filter((i) => i.key !== key), submitting };
}

/** 「批准并继续」: 收起这条提示, 记下这段对话对这条命令的预先批准 (只批一次, 限时)。返回要发的话。 */
export function retryNote(s: ApprovalsState, noteId: string, now: number): { state: ApprovalsState; prompt: string | null } {
  const note = s.notes.find((n) => n.id === noteId);
  if (!note || note.command === null) return { state: s, prompt: null };
  const pre = note.command.trim()
    ? [...s.preapprovals, { sessionId: note.sessionId, command: note.command, until: now + RETRY_PREAPPROVAL_MS }]
    : s.preapprovals;
  return {
    state: { ...s, notes: s.notes.filter((n) => n.id !== noteId), preapprovals: pre },
    prompt: retryAfterTimeoutPrompt(note.command),
  };
}

// ── 动作 ─────────────────────────────────────────────

const set = (fn: (s: ApprovalsState) => ApprovalsState) => useApprovalsStore.setState(fn(useApprovalsStore.getState()));

let ticker: ReturnType<typeof setInterval> | null = null;

function ensureTicker(): void {
  if (ticker !== null) return;
  ticker = setInterval(() => {
    set((s) => expireItems(s, Date.now()));
    if (useApprovalsStore.getState().items.length === 0 && ticker !== null) {
      clearInterval(ticker);
      ticker = null;
    }
  }, 1000);
}

export function receiveApproval(d: PendingApprovalDetail): void {
  if (!d?.approval_session_key) return;
  const now = Date.now();
  const item = itemFromDetail(d, now);
  const { state, preapproved } = addItem(useApprovalsStore.getState(), item, now);
  useApprovalsStore.setState(state);
  if (preapproved) {
    // 员工刚点了「批准并继续」, 小鲶重做时弹的是同一条命令 → 就按那一下批 (只这一次)
    void toolBridgeChatApproval(item.key, "once").catch((err) => {
      // eslint-disable-next-line no-console
      console.warn("[approval] 预先批准送回执失败:", err);
      addNote({ id: `error:${item.key}`, sessionId: item.sessionId, text: approvalErrorMessage(err), command: null });
    });
    return;
  }
  ensureTicker();
  // 横幅只在对话界面里; 人在别的标签页 / 别的 App 就不知道要批准。窗口在眼前就不弹。只弹一次 (原来两个横幅各弹一次)
  if (shouldNotifyApproval(typeof document === "undefined" ? undefined : document)) {
    const n = approvalNotification(item.command, item.timeoutS);
    void sendNotification(n.title, n.body).catch(() => {});
  }
}

function addNote(note: ApprovalNote): void {
  set((s) => ({ ...s, notes: [...s.notes.filter((n) => n.id !== note.id), note] }));
}

export async function decideApproval(key: string, choice: ApprovalChoice): Promise<void> {
  const s = useApprovalsStore.getState();
  const item = s.items.find((i) => i.key === key);
  if (!item || s.submitting[key]) return;
  useApprovalsStore.setState({ submitting: { ...s.submitting, [key]: choice } });
  try {
    await toolBridgeChatApproval(key, choice);
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn("[approval] 送回执失败:", err);
    // 409 = 已经超时作废 (点晚了), 一样可以「批准并继续」
    const late = choice !== "deny" && /\b409\b/.test(String(err));
    addNote({ id: `error:${key}`, sessionId: item.sessionId, text: approvalErrorMessage(err), command: late ? item.command : null });
  }
  set((st) => removeItem(st, key));
}

export function dismissApprovalNote(id: string): void {
  set((s) => ({ ...s, notes: s.notes.filter((n) => n.id !== id) }));
}

/** 返回要发进那段对话的话; 调用方负责发 (只有那段对话的界面能发)。 */
export function retryApprovalNote(id: string): string | null {
  const { state, prompt } = retryNote(useApprovalsStore.getState(), id, Date.now());
  useApprovalsStore.setState(state);
  return prompt;
}

// 进程里只装一次监听 —— 不跟哪个界面的生命周期走 (工作台切走标签页会卸载)
let installed = false;
export function installApprovalListener(): void {
  if (installed || typeof window === "undefined") return;
  installed = true;
  window.addEventListener("catfish:approval-pending", ((e: CustomEvent<PendingApprovalDetail>) => {
    receiveApproval(e.detail);
  }) as EventListener);
  // hermes 实际等多久 (config.yaml approvals.timeout) —— 事件里不带, 读一次
  hermesApprovalTimeout().then(setApprovalTimeoutS).catch(() => {});
}

/** 测试用 */
export function resetApprovalsForTest(): void {
  useApprovalsStore.setState({ ...empty });
}
