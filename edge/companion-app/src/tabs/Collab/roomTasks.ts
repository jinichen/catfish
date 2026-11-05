/**
 * 群里派任务的小工具 (11/5, docs/GROUP-CHAT-PLAN.md 第二步) —— 都是纯函数, 好测。
 *
 * 「@小鲶 让张三、李四周五前把拜访记录交上来」→ 起草一张任务单 (要做什么 / 给谁 / 截止), 打开派任务弹层给发起人改了再发。
 * 起草按规则来 (认群成员的名字和常见的时间说法), 不调模型: 快、不出错, 认不出的留空让人自己填。
 */
import type { OwnerRow } from "../../lib/tauri_colleague_tasks";
import type { RoomMember, RoomMessage, RoomTaskState } from "../../lib/tauri_colleague_rooms";
import type { TaskDraft } from "./TaskComposer";

export const XIAONIAN = "@小鲶";

export const isTaskAsk = (text: string): boolean => text.includes(XIAONIAN);

const WD: Record<string, number> = { 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 日: 0, 天: 0 };
const pad = (n: number) => String(n).padStart(2, "0");
const local = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;

/** 认文字里的截止时间 → datetime-local 的「YYYY-MM-DDTHH:mm」; 认不出 = ""。只写日子不写钟点 = 当天 18:00。 */
export function parseDeadline(text: string, now: Date = new Date()): string {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const plus = (n: number) => new Date(today.getFullYear(), today.getMonth(), today.getDate() + n);
  let day: Date | null = null;
  let m: RegExpMatchArray | null;
  if ((m = text.match(/(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})/))) {
    day = new Date(+m[1], +m[2] - 1, +m[3]);
  } else if ((m = text.match(/(\d{1,2})月(\d{1,2})[日号]/))) {
    day = new Date(today.getFullYear(), +m[1] - 1, +m[2]);
    if (day < today) day.setFullYear(day.getFullYear() + 1);
  } else if (text.includes("后天")) {
    day = plus(2);
  } else if (text.includes("明天") || text.includes("明早") || text.includes("明晚")) {
    day = plus(1);
  } else if ((m = text.match(/(下个?)?(?:周|星期|礼拜)([一二三四五六日天])/))) {
    const target = WD[m[2]];
    const cur = today.getDay();
    if (m[1]) {
      const toMonday = ((1 - cur + 7) % 7) || 7;
      day = plus(toMonday + ((target + 6) % 7));
    } else {
      day = plus((target - cur + 7) % 7);
    }
  } else if (/今天|今晚|下班前/.test(text)) {
    day = today;
  }

  let hour: number | null = null;
  let minute = 0;
  if ((m = text.match(/(上午|中午|下午|晚上|早上)?\s*(\d{1,2})(?:[点:：时](半|(\d{1,2})分?)?)/))) {
    hour = +m[2];
    minute = m[3] === "半" ? 30 : m[4] ? +m[4] : 0;
    if ((m[1] === "下午" || m[1] === "晚上") && hour < 12) hour += 12;
    if (hour > 23 || minute > 59) hour = null;
  } else if (text.includes("中午")) {
    hour = 12;
  } else if (text.includes("明早") || text.includes("上午")) {
    hour = 10;
  }
  if (!day && hour === null) return "";
  if (!day) {
    day = today;
    if (hour !== null && (hour < now.getHours() || (hour === now.getHours() && minute <= now.getMinutes()))) day = plus(1);
  }
  const at = new Date(day.getFullYear(), day.getMonth(), day.getDate(), hour ?? 18, hour === null ? 0 : minute);
  return local(at);
}

const display = (m: { email: string; name: string }) => m.name || m.email.split("@")[0];

/** 「@小鲶 让张三、李四周五前把拜访记录交上来」→ 任务草稿。收件人 = 文字里出现了名字 (带不带 @ 都算) 的已加入成员。 */
export function draftFromText(text: string, members: RoomMember[], me: string, now: Date = new Date()): TaskDraft {
  const joined = members.filter((m) => m.state === "joined" && m.email !== me);
  const recipients = joined.filter((m) => text.includes(display(m))).map((m) => m.email);
  const body = text.split(XIAONIAN).join("").replace(/\s+/g, " ").trim().replace(/^[,，、]\s*/, "").replace(/^(帮我)?(让|请|叫)/, "").trim();
  return { text: body, recipients, deadline: parseDeadline(text, now) };
}

export interface CardState { email: string; name: string; state: RoomTaskState | "waiting"; period?: number }

/** 任务卡上每个人现在的状态: 按群里收到的状态消息, 后来的覆盖先前的。 */
export function cardStates(taskId: string, assignees: string[], messages: RoomMessage[], members: RoomMember[]): CardState[] {
  const last = new Map<string, { state: RoomTaskState; period?: number }>();
  for (const m of messages) {
    if (m.kind === "status" && m.status?.task_id === taskId && m.from) last.set(m.from, { state: m.status.state, period: m.status.period });
  }
  return assignees.map((email) => {
    const mem = members.find((x) => x.email === email);
    const s = last.get(email);
    return { email, name: mem ? display(mem) : email.split("@")[0], state: s?.state ?? "waiting", period: s?.period };
  });
}

export const STATE_WORD: Record<CardState["state"], string> = {
  waiting: "还没回", accepted: "在做", done: "已交回", declined: "不接", quit: "退出了",
};

/** 发起人「把汇总发到群里」: 进度 + 每人交回的结果 (各截 200 字), 填进输入框给他改了再发。 */
export function summaryText(title: string, rows: OwnerRow[], nameOf: (email: string) => string): string {
  const lines = rows.map((r) => {
    const res = r.result ? `: ${r.result.length > 200 ? `${r.result.slice(0, 200)}…` : r.result}` : "";
    return `· ${nameOf(r.email)} ${r.state}${res}`;
  });
  return `「${title}」汇总\n${lines.join("\n")}`.slice(0, 4000);
}
