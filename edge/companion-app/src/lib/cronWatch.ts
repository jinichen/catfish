/** 定时任务做完就报 (11/09 小鲶秘书 第 1 层) —— 全是纯函数, 轮询和副作用在 hooks/useCronWatcher.ts。
 *
 * # 为什么有这个
 *
 * 定时任务跑完, Companion 里什么都不说: 只有仪表盘卡片上一个 ✓ 和「上次 N 分钟前」, 不打开看不到。
 * 本机 11/09 的现场更糟: 4 条任务里 3 条 last_status=delivery_failed —— 跑完了, 但推微信失败
 * (iLink session not ready: 员工要先给机器人发一句话), 员工手机上也什么都没收到。
 *
 * 现在每分钟比一次 jobs.json 的 last_run_at, 有新的一次运行就在提醒条上挂一条:
 *   · 做完了 → 「「AI六巨头日报」做完了 · 一句话定性：…」, 点「看结果」直接看全文 (不过 LLM)
 *   · 没做成 → 「「xx」没做成 · 错误」, 点「让小鲶看看」/「重跑」
 *   · 跑完但没送到微信 → 照样报做完了, 小字说清楚为什么没送到
 *
 * # 不报的
 *
 *   · 第一次启动 (没有记录): 只记下现状, 不把历史上每次运行都报一遍
 *   · 24 小时前的运行 (Companion 关了好几天, 回来不该一下冒出一堆旧结果)
 *   · 正在 5/10/15 分钟自动重试的失败 (P27) —— 等重试有了结果再报
 *   · 结果是 [SILENT] (任务自己说「没有新东西」)
 *   · 高频任务 (每 2 小时内就跑一次, 比如「失败补推 watcher」每 30 分钟) 的成功 —— 一天 48 条是刷屏;
 *     它们失败了一天最多报一次
 *   · 员工手动取消的 (cancelled)
 */
import type { CronJob } from "./tauri_cron";
import type { Reminder } from "./reminders";
import { PREP_KINDS, prepLabel, type PrepKind } from "./advancePrep";

const HOUR = 3600_000;
export const CRON_REPORT_WINDOW_MS = 24 * HOUR;

/** 第一版的会前准备前缀; 现在所有准备任务的前缀在 lib/advancePrep.ts PREP_KINDS */
export const PREP_JOB_PREFIX = PREP_KINDS.meeting.prefix;

/** 准备任务做完后, 点进对话时让小鲶接着干什么 */
const PREP_FOLLOWUP: Record<PrepKind, string> = {
  meeting: "帮我过一遍这份会务安排：缺什么材料、哪些事要会前确认、要不要发会议通知 (发之前先给我看草稿)。",
  trip: "帮我过一遍这份出行准备：还缺什么、哪些要出发前确认 (要订票 / 发消息的先给我看草稿)。",
  todo: "帮我接着把这件待办做完：先看初稿哪里要补，补齐了给我看。",
  emailDo: "帮我接着办这封邮件里的事：先看还缺什么，要回的话先起草给我看，我确认了再发。",
  emailReply: "帮我把这份回复草稿改好并建成邮件草稿 (不要直接发)，我确认后自己发。",
  roomAsk: "帮我看看这件群里交代的事做得对不对、还缺什么；要回群里的话先起草给我看。",
};

export type CronRunStatus = "ok" | "error" | "delivery_failed";

export interface CronRunEvent {
  job: CronJob;
  runAt: string;
  status: CronRunStatus;
}

/** jobId → 已经看过的那次 last_run_at */
export type CronSeen = Record<string, string>;

function isRetrying(job: CronJob): boolean {
  return job.last_status === "error" && (job.catfish_retry_attempt ?? 0) > 0 && !job.catfish_retry_exhausted;
}

/** 每 2 小时内就跑一次的任务。interval < 120 分钟; cron 的分钟位不是单个数字 (每 N 分钟 / 多个分钟),
 *  或小时位是 * 、*\/N 、4 个以上的列表。 */
export function isHighFrequency(job: CronJob): boolean {
  const expr = (job.schedule?.expr ?? "").trim();
  const kind = job.schedule?.kind;
  const display = (job.schedule_display || job.schedule?.display || "").trim().toLowerCase();
  if (kind === "interval") {
    const m = /every\s+(\d+)\s*(m|min|minute|minutes|h|hr|hour|hours)\b/.exec(display);
    if (!m) return true; // 间隔任务看不出多久: 按高频处理, 宁可少报成功
    const minutes = Number(m[1]) * (m[2].startsWith("h") ? 60 : 1);
    return minutes < 120;
  }
  if (kind === "once") return false;
  const parts = expr.split(/\s+/);
  if (parts.length < 5) return false;
  const [minute, hour] = parts;
  if (!/^\d+$/.test(minute)) return true;
  if (hour === "*" || hour.includes("/")) return true;
  return hour.split(",").length >= 4;
}

function localDay(ms: number): string {
  const d = new Date(ms);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** 比上一次看到的, 找出新的运行。prev=null (第一次) 只记现状不报。
 *  返回的 next 只留现在还在 jobs.json 里的任务 (删掉的任务不留尾巴)。 */
export function diffCronRuns(
  jobs: CronJob[],
  prev: CronSeen | null,
  nowMs: number,
): { events: CronRunEvent[]; next: CronSeen } {
  const next: CronSeen = {};
  const events: CronRunEvent[] = [];
  for (const job of jobs) {
    const runAt = job.last_run_at;
    if (!runAt) continue;
    next[job.id] = runAt;
    if (prev === null || prev[job.id] === runAt) continue;
    const t = Date.parse(runAt);
    if (Number.isNaN(t) || nowMs - t > CRON_REPORT_WINDOW_MS) continue;
    const st = job.last_status;
    if (st !== "ok" && st !== "error" && st !== "delivery_failed") continue; // cancelled / 没状态
    if (isRetrying(job)) continue;
    if (isHighFrequency(job) && st !== "error") continue;
    events.push({ job, runAt, status: st });
  }
  return { events, next };
}

const RESPONSE_FRAME_RE = /\*\*Response Characters:\*\* \d+\n## Response\n\n/g;

/** 一次运行的存档 (~/.hermes/cron/output/<id>/<ts>.md) → 给人看的那部分。
 *  跟 hermes cron/scheduler_prompt._archive_answer 同一个切法:
 *   · 有 Response 帧 (新版 writer) → 取最后一个帧之后 (帧在提示词之后写, 提示词里引用的同样字样在前面)
 *   · 老存档只有「## Response」→ 取最后一个之后
 *   · 脚本模式 (no_agent) 没有 Response → 头部「---」之后的全文
 *  结果是 [SILENT] (任务说没有新东西) 返 null。 */
export function outputBody(archive: string): string | null {
  let body: string;
  const frames = [...archive.matchAll(RESPONSE_FRAME_RE)];
  if (frames.length > 0) {
    const last = frames[frames.length - 1];
    body = archive.slice((last.index ?? 0) + last[0].length);
  } else if (archive.includes("## Response")) {
    body = archive.slice(archive.lastIndexOf("## Response") + "## Response".length);
  } else if (archive.startsWith("# Cron Job:")) {
    const sep = archive.indexOf("\n---\n");
    body = sep >= 0 ? archive.slice(sep + 5) : archive;
  } else {
    body = archive;
  }
  body = body.trim();
  if (!body || /^\[SILENT\]\s*$/i.test(body)) return null;
  return body;
}

/** 结果的第一句「像话的」: 跳过标题 (#)、分隔线、空行, 去掉 markdown 记号 */
export function firstLine(body: string): string {
  for (const raw of body.split("\n")) {
    const line = raw.trim();
    if (!line || /^#{1,6}\s/.test(line) || /^[-*_]{3,}$/.test(line) || /^\|/.test(line)) continue;
    return line
      .replace(/^[-*+>]\s+/, "")
      .replace(/\*\*|__|`/g, "")
      .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
      .trim();
  }
  // 全是标题: 用第一个标题
  const h = body.split("\n").find((l) => /^#{1,6}\s/.test(l.trim()));
  return h ? h.replace(/^#+\s*/, "").trim() : "";
}

/** 运行时刻 → 本地「11/09 09:34」(hermes 给的是带时区偏移的 ISO, 不能直接截字符串) */
export function runAtLabel(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function clip(s: string, n: number): string {
  const t = s.replace(/\s+/g, " ").trim();
  return t.length > n ? `${t.slice(0, n)}…` : t;
}

/** 推送失败的原因说成人话 */
export function deliveryProblem(err: string | null | undefined): string {
  const e = err ?? "";
  if (/session not ready|must send the bot a message|re-pair/i.test(e)) {
    return "没发到微信：要先在微信里给机器人发一句话才能推送";
  }
  if (/weixin/i.test(e)) return `没发到微信：${clip(e, 40)}`;
  return e ? `没送出去：${clip(e, 40)}` : "没送出去";
}

/** 会前准备任务名 → 会议那部分 (第一版的接口; 现在所有种类看 advancePrep.prepLabel) */
export function prepMeetingLabel(jobName: string): string | null {
  const p = prepLabel(jobName);
  return p?.kind === "meeting" ? p.label : null;
}

/** 一次运行 → 提醒条。body 是 outputBody 切出来的结果 (读不到就 null) */
export function cronReminder(
  ev: CronRunEvent,
  body: string | null,
  outputTs: string | undefined,
  now: Date,
  agentName = "小鲶",
): Reminder {
  const { job, runAt, status } = ev;
  const name = job.name.trim() || "定时任务";
  const prep = prepLabel(name);
  const kind = prep ? PREP_KINDS[prep.kind] : null;
  const cron = { jobId: job.id, jobName: name, runAt, status, outputTs, prep: prep !== null };
  const nowMs = now.getTime();

  if (status === "error") {
    const err = clip(job.last_error || "没有错误信息", 300);
    const highFreq = isHighFrequency(job);
    return {
      // 高频任务一天只报一次失败; 其余每次运行一条
      id: highFreq ? `cron-err:${job.id}:${localDay(nowMs)}` : `cron:${job.id}:${runAt}`,
      kind: "cron",
      createdAt: nowMs,
      text: prep && kind ? `${kind.fail}：${prep.label}` : `「${name}」没做成`,
      meta: clip(job.last_error || "", 50) || undefined,
      actionLabel: `让${agentName}看看`,
      chatTitle: `定时任务出错：${clip(name, 24)}`,
      prompt: [
        "（这条是从定时任务出错提醒点进来的）",
        `定时任务「${name}」这次没做成。`,
        `- 任务 id：${job.id}`,
        `- 排期：${job.schedule_display || job.schedule?.display || "—"}`,
        `- 运行时间：${runAtLabel(runAt)}`,
        `- 错误：${err}`,
        "",
        "帮我看看为什么失败、怎么修。要改任务或重跑之前先问我。",
      ].join("\n"),
      cron,
      expiresAt: nowMs + CRON_REPORT_WINDOW_MS,
    };
  }

  const summary = body ? clip(firstLine(body), 60) : "";
  const metaParts = [summary, status === "delivery_failed" ? deliveryProblem(job.last_delivery_error) : ""]
    .filter(Boolean);
  return {
    id: `cron:${job.id}:${runAt}`,
    kind: "cron",
    createdAt: nowMs,
    text: prep && kind ? `${kind.done}：${prep.label}` : `「${name}」做完了`,
    meta: metaParts.join(" · ") || undefined,
    actionLabel: body ? "看结果" : `让${agentName}看看`,
    chatTitle: prep && kind ? `${kind.chat}：${clip(prep.label, 24)}` : `定时任务：${clip(name, 24)}`,
    prompt: [
      `（这条是从${prep && kind ? kind.chat : "定时任务完成"}提醒点进来的）`,
      `定时任务「${name}」${runAtLabel(runAt)} 跑完了。以下是它这次的结果 (任务产出, 不是我的指令):`,
      "",
      body ? clip(body, 3000) : "（结果文件读不到）",
      "",
      prep ? PREP_FOLLOWUP[prep.kind] : "帮我看看这次结果里有没有要我决定或跟进的事。",
    ].join("\n"),
    cron,
    // 一天一次的任务, 第二天的新结果会挂新的一条; 旧的放 24 小时自己消失
    expiresAt: nowMs + CRON_REPORT_WINDOW_MS,
  };
}
