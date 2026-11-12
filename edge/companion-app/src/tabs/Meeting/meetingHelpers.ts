/** 会议页的纯函数 (10/1) —— 单测在 meetingHelpers.test.ts。 */
import type { WikiFileInfo } from "../../lib/tauri_wiki";
import type { ActionItem, MeetingStatus, RecorderStatus, SysApp, SysPick, SysSources, SystemTrackStatus } from "../../lib/tauri_meeting";

/** 进热词的知识库条目类型: 人名 / 项目 / 单位 / 部门 —— 识别最容易错、纪要里最要紧的就是这些。 */
const HOTWORD_SUBTYPES = new Set(["person", "project", "org", "organization", "department"]);
/** 跟 Rust meeting_store::normalize_hotwords 一致: 太长的不像热词, 太多反而干扰识别。 */
const MAX_HOTWORD_CHARS = 20;
const MAX_HOTWORDS = 100;

export function wikiHotwords(files: WikiFileInfo[]): string[] {
  const out: string[] = [];
  for (const f of files) {
    if (f.kind !== "entity" || !f.subtype || !HOTWORD_SUBTYPES.has(f.subtype)) continue;
    if (f.ontology_status === "pending") continue;
    for (const w of [f.title, ...(f.aliases ?? [])]) {
      const t = w.trim();
      if (t && [...t].length <= MAX_HOTWORD_CHARS && !out.includes(t)) out.push(t);
    }
  }
  return out.slice(0, MAX_HOTWORDS);
}

export function mergeHotwords(current: string, extra: string[]): string {
  const have = current.split(/[\s,，、]+/).filter(Boolean);
  for (const w of extra) if (!have.includes(w)) have.push(w);
  return have.join(" ");
}

export const STATUS_LABEL: Record<MeetingStatus, string> = {
  created: "未开始",
  recording: "录音中",
  recorded: "待转写",
  transcribing: "转写中",
  transcribed: "已转写",
  failed: "转写失败",
};

/** 待办 → 任务库参数。source_id 带序号, 重复写同一条不会出两个任务 (task_library 按它去重)。 */
export function actionItemToTask(meetingId: string, title: string, index: number, it: ActionItem) {
  const owner = it.owner ? `负责人: ${it.owner}\n` : "";
  return {
    title: it.task,
    due_date_iso: it.due || undefined,
    body: `${owner}来自会议「${title}」的纪要`,
    source: "meeting",
    source_id: `${meetingId}#${index}`,
  };
}

/** 转写进度估算: 实测 M4 上约 8 倍实时; 老机器慢, 封顶 95% 等真的完成事件。 */
export function estimatePercent(elapsedSecs: number, audioSecs: number | null | undefined): number | null {
  if (!audioSecs || audioSecs <= 0) return null;
  return Math.min(95, Math.floor((elapsedSecs / (audioSecs / 8)) * 100));
}

/** 知识库条目名的规则 (tool-bridge wiki_files._slugify / _validate_slug, 跟 Rust wiki_write.rs 对齐):
 *  只能有字母数字 (含中文)、-、_ 和空格 (空格转成 -), 转完不超过 100 字节 (约 33 个汉字)。
 *  「·」「/」「:」这类一律不行 —— 10/1 第一版用了「·」, 存知识库直接被拒。 */
const SLUG_MAX_BYTES = 100;

function slugBytes(title: string): number {
  return new TextEncoder().encode(title.trim().replace(/\s+/g, "-")).length;
}

export function minutesWikiTitle(meetingTitle: string, date: string): string {
  const clean = meetingTitle.replace(/[^\p{L}\p{N}_\- ]+/gu, " ").replace(/\s+/g, " ").trim();
  const make = (t: string) => ["会议纪要", t, date].filter(Boolean).join(" ");
  let t = clean;
  while (t && slugBytes(make(t)) > SLUG_MAX_BYTES) t = [...t].slice(0, -1).join("").trim();
  return make(t);
}

/** minutes.md 去掉首行 H1 (标题已经是条目名) 当条目正文。 */
export function minutesWikiBody(markdown: string): string {
  return markdown.replace(/^# .*\n+/, "");
}

/** 录音中的淡色提示 (11/06): 电脑太忙丢了几小段声音 / 系统换了麦克风 —— 都不是错误, 录音照常继续。
 *  真出错 (rec.error) 时只显红字, 这些不再叠上去。 */
export function recorderNotices(rec: Pick<RecorderStatus, "error" | "glitches" | "notice">): string[] {
  if (rec.error) return [];
  const out: string[] = [];
  if ((rec.glitches ?? 0) > 0) {
    out.push(`电脑有点忙, 有 ${rec.glitches} 次丢了一小段声音 (每次零点几秒), 录音还在继续。重要的会可以先关掉占资源的程序。`);
  }
  if (rec.notice) out.push(rec.notice);
  return out;
}

// ── 11/11 线上会议: 录会议软件放出来的对方声音 ──

/** 选项的 key: 程序按 bundle id (没有就按名字); "" = 全部电脑声音 */
export const sysAppKey = (a: SysApp): string => a.bundle_id || `name:${a.name}`;

/** 默认选哪个: 正在出声的会议软件 > 开着的会议软件 (微信除外 —— 开着不等于在开会) > 全部电脑声音 */
export function defaultSysKey(src: SysSources): string {
  if (src.mode !== "tap" && src.mode !== "sck") return "";
  const meetings = src.apps.filter((a) => a.meeting);
  const playing = meetings.find((a) => a.playing);
  if (playing) return sysAppKey(playing);
  const open = meetings.find((a) => !/xinWeChat/i.test(a.bundle_id));
  return open ? sysAppKey(open) : "";
}

export function sysPickFor(src: SysSources, key: string): SysPick {
  const app = key ? src.apps.find((a) => sysAppKey(a) === key) : undefined;
  if (!app) return { name: "", bundle_id: null, pids: [] };
  // bundle id 优先: 会议开着开着换了帮手进程 (进出会议室) 也认得; 没有 bundle id 才按进程号
  return { name: app.name, bundle_id: app.bundle_id || null, pids: app.bundle_id ? [] : app.pids };
}

/** 本地人数: 1 ≤ n ≤ 参会人数-1 (至少留一个对方); 参会人数没填时只管 ≥1 */
export function clampLocalSpeakers(raw: string, attendees: number | null): number {
  const n = Math.floor(Number(raw));
  const lo = Number.isFinite(n) && n >= 1 ? n : 1;
  return attendees && attendees > 1 ? Math.min(lo, attendees - 1) : lo;
}

/** 对方那条连续没声音多久才提示 —— 会议里几十秒没人说话很正常, 不能 5 秒就报 */
export const SYSTEM_SILENT_WARN_SECS = 30;

export function systemTrackNotice(sys: SystemTrackStatus, silentSecs: number, hint: string): string | null {
  if (sys.error) return null; // 错误另外红字显示
  if (silentSecs < SYSTEM_SILENT_WARN_SECS) return null;
  return `「${sys.source}」${Math.floor(silentSecs)} 秒没有声音。会议里暂时没人说话就没关系; 对方明明在说却一直是 0, 多半是没给权限 —— ${hint}`;
}

/** 11/12「复制」: 纪要 Markdown → 能直接贴进微信 / 群里的纯文本 (去掉 # ** 这些符号, 待办的 [ ] 换成 ☐)。 */
export function minutesPlainText(markdown: string): string {
  return markdown
    .split("\n")
    .map((l) => l
      .replace(/^#{1,6}\s+(.*)$/, "【$1】")
      .replace(/^(\s*)[-*]\s+\[\s?\]\s+/, "$1☐ ")
      .replace(/^(\s*)[-*]\s+\[x\]\s+/i, "$1☑ ")
      .replace(/^(\s*)[-*]\s+/, "$1· ")
      .replace(/\*\*(.+?)\*\*/g, "$1")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/^---+$/, ""))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/** 11/12 改转写错字后, 建议记成常用热词的词: 新句子里新出现的连续片段 (2–20 字), 最多 5 个。
 *  例: 「西西RC末次」→「CC RC末次」给出「CC RC」。纯标点 / 空白的改动不算。 */
export function changedWords(orig: string, next: string): string[] {
  const a = [...orig];
  const b = [...next];
  // 最长公共子序列, 标出 next 里哪些字是原来就有的
  const dp: number[][] = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const kept = new Array(b.length).fill(false);
  for (let i = 0, j = 0; i < a.length && j < b.length;) {
    if (a[i] === b[j]) { kept[j] = true; i++; j++; } else if (dp[i + 1][j] >= dp[i][j + 1]) i++; else j++;
  }
  const isWord = (c: string) => /[A-Za-z0-9]/.test(c);
  const trimP = (w: string) => w.replace(/^[\s，。、,.!?！？：:；;]+|[\s，。、,.!?！？：:；;]+$/g, "");
  const out: string[] = [];
  let j = 0;
  while (j < b.length) {
    if (kept[j]) { j++; continue; }
    let s = j;
    while (j < b.length && !kept[j]) j++;
    let e = j;
    // 英文 / 数字连着的带上 (「CC」+「RC」→「CC RC」)
    while (s > 0 && (isWord(b[s - 1]) || (b[s - 1] === " " && s > 1 && isWord(b[s - 2])))) s--;
    while (e < b.length && (isWord(b[e]) || (b[e] === " " && e + 1 < b.length && isWord(b[e + 1])))) e++;
    // 只改了一个汉字 (「鸾总」→「栾总」): 带上后面一个字, 不然记不成词
    if (e - s === 1 && e < b.length && !/[\s，。、,.!?！？：:；;]/.test(b[e])) e++;
    const w = trimP(b.slice(s, e).join(""));
    if ([...w].length >= 2 && [...w].length <= 20 && !out.includes(w)) out.push(w);
  }
  return out.slice(0, 5);
}
