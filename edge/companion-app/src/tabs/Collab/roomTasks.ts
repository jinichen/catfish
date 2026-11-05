/**
 * 群里派任务 (11/5, docs/GROUP-CHAT-PLAN.md 第二步) —— 在聊天里一句话派, 不弹窗、不用卡片。
 *
 * 「@张三 @李四 周五前把拜访记录交上来」「@张三 每周五下午5点交周报, 做4次」「先让@张三 收本月数据, 再让@李四 汇总成月报」
 * → 认出 派给谁 (@ 到的已加入成员) / 截止 / 定时 / 周期 / 分几段, 输入框下面实时显示认出来的, 员工点「派给@的人」才发。
 * 按规则认, 不调模型: 认不出的不猜 —— 显示出来让人改那句话。
 */
import type { OwnerRow, Schedule } from "../../lib/tauri_colleague_tasks";
import type { RoomMember, RoomMessage, RoomTaskState } from "../../lib/tauri_colleague_rooms";

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

/** 文字里 @ 到的已加入成员 (不含自己)。 */
export function atJoined(text: string, members: RoomMember[], me: string): string[] {
  return members.filter((m) => m.state === "joined" && m.email !== me && text.includes(`@${display(m)}`)).map((m) => m.email);
}

const CN_NUM: Record<string, number> = { 一: 1, 两: 2, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10 };
const num = (s: string) => (/^\d+$/.test(s) ? +s : CN_NUM[s] ?? NaN);

/** 时分: 「下午5点」「9:30」「中午」; 没写 = null。 */
function timeOf(text: string): [number, number] | null {
  const m = text.match(/(上午|中午|下午|晚上|早上)?\s*(\d{1,2})(?:[点:：时](半|(\d{1,2})分?)?)/);
  if (m) {
    let h = +m[2];
    const mi = m[3] === "半" ? 30 : m[4] ? +m[4] : 0;
    if ((m[1] === "下午" || m[1] === "晚上") && h < 12) h += 12;
    return h <= 23 && mi <= 59 ? [h, mi] : null;
  }
  if (text.includes("中午")) return [12, 0];
  return null;
}

/** 周期: 「每天 / 每个工作日 / 每周五 / 每周一、三 / 每月15号」+ 时间 + 结束 (到哪天 / 做几次)。不是周期 = null。 */
export function parseCron(text: string, now: Date = new Date()): { schedule: Schedule | null; label: string; problem?: string } | null {
  const t = timeOf(text) ?? [9, 0];
  const hm = `${t[1]} ${t[0]}`;
  let expr = "";
  let label = "";
  let m: RegExpMatchArray | null;
  const hhmm = `${pad(t[0])}:${pad(t[1])}`;
  if (/每(个)?工作日/.test(text)) {
    expr = `${hm} * * 1-5`; label = `工作日 ${hhmm}`;
  } else if (/每天|每日/.test(text)) {
    expr = `${hm} * * *`; label = `每天 ${hhmm}`;
  } else if ((m = text.match(/每(?:个)?(?:周|星期|礼拜)([一二三四五六日天](?:[、,，和及]?[一二三四五六日天])*)/))) {
    const days = [...new Set([...m[1].replace(/[、,，和及]/g, "")].map((c) => WD[c]))];
    expr = `${hm} * * ${days.join(",")}`;
    label = `每周${[...m[1].replace(/[、,，和及]/g, "")].join("、")} ${hhmm}`;
  } else if ((m = text.match(/每(?:个)?月(\d{1,2})[号日]/))) {
    expr = `${hm} ${+m[1]} * *`; label = `每月 ${+m[1]} 号 ${hhmm}`;
  } else {
    return null;
  }
  let until: string | null = null;
  let times: number | null = null;
  if ((m = text.match(/(?:做|跑|交|发|共|一共)?\s*(\d+|[一两二三四五六七八九十])\s*次/))) times = num(m[1]);
  if (/到年底/.test(text)) until = `${now.getFullYear()}-12-31`;
  else if (/到月底/.test(text)) until = local(new Date(now.getFullYear(), now.getMonth() + 1, 0)).slice(0, 10);
  else if ((m = text.match(/到\s*(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})/))) until = `${m[1]}-${pad(+m[2])}-${pad(+m[3])}`;
  else if ((m = text.match(/到\s*(\d{1,2})月(\d{1,2})[日号]?/))) {
    const d = new Date(now.getFullYear(), +m[1] - 1, +m[2]);
    if (d < new Date(now.getFullYear(), now.getMonth(), now.getDate())) d.setFullYear(d.getFullYear() + 1);
    until = local(d).slice(0, 10);
  }
  if (times !== null && !(times >= 1 && times <= 100)) times = null;
  const end = [until ? `到 ${until} 为止` : "", times ? `做 ${times} 次` : ""].filter(Boolean).join("、");
  if (!until && !times) {
    return { schedule: null, label, problem: "周期任务要说什么时候结束, 比如「做4次」「到年底」「到12月31日」" };
  }
  return { schedule: { kind: "cron", expr, until, times }, label: `${label}, ${end}` };
}

/** 定时做一次: 写了「定时」, 或「X点做 / 跑 / 发 / 执行」。其余的时间都当截止。 */
function parseOnce(text: string, now: Date): { schedule: Schedule; label: string } | null {
  if (!/定时/.test(text) && !/(\d{1,2}[点:：]\d{0,2}|中午)\s*(?:的时候)?(?:帮我|给我)?(?:做|跑|执行|发)/.test(text)) return null;
  const at = parseDeadline(text, now);
  if (!at) return null;
  return { schedule: { kind: "once", at }, label: `${at.replace("T", " ")} 做一次` };
}

export interface ChatStage { name: string; text: string; recipients: string[]; deadline: string }

export type SingleTask = { kind: "task"; text: string; recipients: string[]; deadline: string; schedule: Schedule | null; label: string; problem?: string };

export type ChatTask =
  | SingleTask
  | { kind: "pipeline"; title: string; stages: ChatStage[]; label: string; problem?: string }
  /** 11/5: 多行、每行 @ 了人 (如小鲶整理的待办) —— 一行派一个任务 */
  | { kind: "batch"; items: SingleTask[]; label: string; problem?: string };

const BULLET = /^\s*(?:[-*·•]|\d+[.、)]|[（(]?\d+[)）])\s*/;

const STAGE_SPLIT = /(?:^|[，,。；;\s])(?:先|再|然后|接着|最后)(?=\s*(?:让|请|叫|由)?\s*@)/g;

/** 一句话 → 要派的任务。@ 不到任何已加入的人 = null (就是普通聊天)。 */
export function parseChatTask(text: string, members: RoomMember[], me: string, now: Date = new Date()): ChatTask | null {
  const body = text.trim();
  // 多行待办: 两行以上各自 @ 了人 → 一行一个任务 (前面的「1.」「·」去掉; 没 @ 人的行, 如标题, 不管)
  const lines = body.split(/\n+/).map((l) => l.replace(BULLET, "").trim()).filter((l) => atJoined(l, members, me).length);
  if (lines.length >= 2 && !/^\s*先/.test(body)) {
    const items = lines.map((l) => parseOneOrPipeline(l, members, me, now, false) as SingleTask);
    const bad = items.findIndex((x) => x.problem);
    const count = new Map<string, number>();
    for (const it of items) for (const e of it.recipients) count.set(e, (count.get(e) ?? 0) + 1);
    const nameOf = (e: string) => display(members.find((m) => m.email === e) ?? { email: e, name: "" });
    return {
      kind: "batch", items,
      label: `分别派 ${items.length} 条: ${[...count].map(([e, n]) => `${nameOf(e)} ${n} 条`).join("、")}`,
      ...(bad >= 0 ? { problem: `第 ${bad + 1} 条: ${items[bad].problem}` } : {}),
    };
  }
  return parseOneOrPipeline(body, members, me, now);
}

/** 一段话 → 一个任务 / 多阶段。allowPipeline=false: 逐条派时一行只当一个任务 */
function parseOneOrPipeline(body: string, members: RoomMember[], me: string, now: Date, allowPipeline = true): ChatTask | null {
  const people = atJoined(body, members, me);
  if (!body || !people.length) return null;
  const nameOf = (e: string) => display(members.find((m) => m.email === e) ?? { email: e, name: "" });

  // 多阶段: 「先让@张三 …, 再让@李四 …」(至少两段, 每段都 @ 了人)
  const cuts = [...body.matchAll(STAGE_SPLIT)].map((m) => m.index! + m[0].search(/先|再|然后|接着|最后/));
  if (allowPipeline && cuts.length >= 2 && /先/.test(body)) {
    const parts = cuts.map((c, i) => body.slice(c, cuts[i + 1] ?? body.length).replace(/[，,。；;\s]+$/, ""));
    const stages = parts.map((p, i) => ({
      name: `第 ${i + 1} 段`, text: p.replace(/^(先|再|然后|接着|最后)\s*(让|请|叫|由)?\s*/, "").trim(),
      recipients: atJoined(p, members, me), deadline: parseDeadline(p, now),
    }));
    const empty = stages.findIndex((st) => !st.recipients.length);
    return {
      kind: "pipeline", title: body.replace(/@\S+\s*/g, "").slice(0, 30), stages,
      label: `分 ${stages.length} 段: ${stages.map((st, i) => `${"①②③④⑤⑥⑦⑧"[i]} ${st.recipients.map(nameOf).join("、")}`).join(" → ")}`,
      ...(empty >= 0 ? { problem: `第 ${empty + 1} 段没 @ 到群里已加入的人` } : {}),
      ...(stages.length > 8 ? { problem: "最多 8 段" } : {}),
    };
  }

  const who = `派给 ${people.map(nameOf).join("、")}`;
  const cron = parseCron(body, now);
  if (cron) {
    return { kind: "task", text: body, recipients: people, deadline: "", schedule: cron.schedule,
             label: `${who} · ${cron.label}`, ...(cron.problem ? { problem: cron.problem } : {}) };
  }
  const once = parseOnce(body, now);
  if (once) {
    const past = new Date(once.schedule.kind === "once" ? once.schedule.at : "") < now;
    return { kind: "task", text: body, recipients: people, deadline: "", schedule: once.schedule, label: `${who} · ${once.label}`,
             ...(past ? { problem: "定的时间已经过了" } : {}) };
  }
  const deadline = parseDeadline(body, now);
  return { kind: "task", text: body, recipients: people, deadline, schedule: null,
           label: `${who} · ${deadline ? `截止 ${deadline.replace("T", " ")}` : "不限时"}`,
           ...(people.length > 20 ? { problem: "一次最多派给 20 人" } : {}) };
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
