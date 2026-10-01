/** 会议页的纯函数 (10/1) —— 单测在 meetingHelpers.test.ts。 */
import type { WikiFileInfo } from "../../lib/tauri_wiki";
import type { ActionItem, MeetingStatus } from "../../lib/tauri_meeting";

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
