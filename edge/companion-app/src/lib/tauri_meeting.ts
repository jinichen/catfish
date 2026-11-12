/**
 * 会议纪要 (10/1, docs/MEETING-MINUTES-PLAN.md) —— Rust 命令的前端封装。
 *
 * Rust: commands/meeting.rs (录音) / meeting_asr_cmd.rs (组件包 + 转写) /
 *       meeting_minutes_cmd.rs (说话人 + 纪要)。
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

import { stageFile } from "./uploadStage";

export type MeetingStatus =
  | "created"
  | "recording"
  | "recorded"
  | "transcribing"
  | "transcribed"
  | "failed";

export interface MeetingMeta {
  id: string;
  title: string;
  created_at: string;
  attendees: number;
  hotwords: string[];
  status: MeetingStatus;
  duration_secs: number;
  device: string | null;
  error: string | null;
  /** 11/11 线上会议: 同时录了谁的电脑声音 (「腾讯会议」/「全部电脑声音」); 只录麦克风 = null */
  system_audio?: string | null;
  /** 11/11 线上会议: 这台电脑前说话的人数 (含自己) */
  local_speakers?: number | null;
}

/** 11/11 能录哪些程序放出来的声音 (Rust services/meeting_sysaudio.rs) */
export interface SysApp {
  name: string;
  bundle_id: string;
  pids: number[];
  /** 这会儿正在出声 */
  playing: boolean;
  /** 认得的会议软件 */
  meeting: boolean;
}

export interface SysSources {
  /** tap / sck: Mac, 可以只录某个程序; loopback: Windows, 录全部电脑声音; none: 录不了 */
  mode: "tap" | "sck" | "loopback" | "none";
  apps: SysApp[];
  hint: string;
}

/** 录哪个程序的声音; bundle_id 和 pids 都空 = 全部电脑声音 */
export interface SysPick {
  name: string;
  bundle_id: string | null;
  pids: number[];
}

export interface SystemTrackStatus {
  source: string;
  level: number;
  seconds: number;
  mode: string;
  error: string | null;
}

export interface InputDevice {
  name: string;
  is_default: boolean;
}

export interface RecorderStatus {
  recording: boolean;
  seconds: number;
  /** 0–1 峰值; 一直是 0 = 麦克风没收到声音 */
  level: number;
  device: string;
  sample_rate: number;
  dir: string | null;
  error: string | null;
  limit_reached: boolean;
  /** 11/06: 电脑太忙时丢了几小段声音 (每次几十毫秒), 录音照常继续 —— 不是错误 */
  glitches?: number;
  /** 11/06: 不影响录音的提示 (系统换了麦克风, 自动跟过去了) */
  notice?: string | null;
  /** 11/11: 同时在录的电脑声音 (线上会议对方); 只录麦克风时 null */
  system?: SystemTrackStatus | null;
}

export interface AsrInstalled {
  version: string;
  python: string;
  models: string;
}

export interface AsrStatus {
  installed: AsrInstalled | null;
  pack_ready: string | null;
  installing: boolean;
  transcribing: boolean;
}

export interface TranscriptSegment {
  spk: number;
  start: number;
  end: number;
  text: string;
  /** 11/12: 员工改过这一句 —— 这是识别出来的原话 */
  orig_text?: string;
  /** 11/11 线上会议: mic = 这台电脑前的人, system = 会议软件里的对方 */
  track?: "mic" | "system";
}

export interface Transcript {
  duration_secs: number;
  speakers: number;
  speakers_requested: number;
  segments: TranscriptSegment[];
}

export interface ActionItem {
  owner: string;
  task: string;
  due: string;
}

export interface MinutesJson {
  meeting_id: string;
  summary: string;
  decisions: string[];
  action_items: ActionItem[];
  open_questions: string[];
  /** 11/3: 上次同系列会议遗留的事在本次的进展 (老纪要没有这一项) */
  carried_over?: string[];
  /** 11/3: 生成时参考的背景材料 (知识库条目 / 上次会议); 老纪要没有 */
  references?: MinutesReferences;
  /** 10/3: 按自定义模版生成的才有; 没有 = 缺省格式 */
  template?: { id: string; name: string; kind?: TemplateKind };
  /** 10/3: 按 Word / Excel 模版填好的文件 (在会议目录里) */
  output_file?: string;
  /** 11/12: 员工改过纪要的时间; 有它「重新生成」要先提醒会覆盖 */
  edited_at?: string;
}

export interface MinutesReferences {
  wiki: { title: string; rel_path: string }[];
  previous_meeting: { meeting_id: string; title: string; date: string } | null;
}

/** 11/3: 「参考的背景材料: 知识库「X」「Y」; 上次会议「T」(日期)」, 没有参考就空串 */
export function referencesNote(refs?: MinutesReferences): string {
  if (!refs) return "";
  const bits: string[] = [];
  if (refs.wiki.length) bits.push(`知识库 ${refs.wiki.map((w) => `「${w.title}」`).join("、")}`);
  if (refs.previous_meeting) bits.push(`上次会议「${refs.previous_meeting.title}」(${refs.previous_meeting.date})`);
  return bits.length ? `参考的背景材料: ${bits.join("; ")}` : "";
}

export type TemplateKind = "markdown" | "docx" | "xlsx";

/** Word / Excel 模版里认出的一个要填的空 */
export interface TemplateSlot {
  id: string;
  label: string;
  kind: "text" | "table";
  columns?: string[];
}

/** 会议纪要自定义模版 (10/3), 存在 ~/.catfish/meeting-templates/ */
export interface MinutesTemplate {
  id: string;
  name: string;
  body: string;
  updated_at: string;
  /** 老数据没有这个字段 = markdown */
  kind?: TemplateKind;
  original_name?: string;
  slots?: TemplateSlot[];
}

/** 缺省模版 = 原来那份固定格式, 不落盘、不能改 */
export const DEFAULT_TEMPLATE_ID = "default";

export interface Minutes {
  json: MinutesJson;
  markdown: string;
  path: string;
}

export const meetingList = () => rawInvoke<MeetingMeta[]>("meeting_list");
export const meetingDelete = (id: string) => rawInvoke<void>("meeting_delete", { id });
export const meetingCreate = (title: string, attendees: number, hotwords: string[]) =>
  rawInvoke<MeetingMeta>("meeting_create", { title, attendees, hotwords });
export const meetingAudioDevices = () => rawInvoke<InputDevice[]>("meeting_audio_devices");
export const meetingRecordStart = (
  id: string,
  device: string | null,
  system: SysPick | null = null,
  localSpeakers: number | null = null,
) => rawInvoke<RecorderStatus>("meeting_record_start", { id, device, system, localSpeakers });
export const meetingSystemAudioSources = () => rawInvoke<SysSources>("meeting_system_audio_sources");
export const meetingRecordStatus = () => rawInvoke<RecorderStatus>("meeting_record_status");
export const meetingRecordStop = () => rawInvoke<MeetingMeta | null>("meeting_record_stop");
/** 10/11 导入录音笔 / 手机录音: 原生选择器 (取消 = null) + 解码进一场新会议 (状态「已录」) */
export const meetingImportPickFile = () => rawInvoke<string | null>("meeting_import_pick_file");
export const meetingImportAudio = (path: string, title: string, attendees: number, hotwords: string[]) =>
  rawInvoke<MeetingMeta>("meeting_import_audio", { path, title, attendees, hotwords });

export const meetingAsrStatus = () => rawInvoke<AsrStatus>("meeting_asr_status");
export const meetingAsrInstall = () => rawInvoke<void>("meeting_asr_install");
export const meetingTranscribe = (id: string) => rawInvoke<void>("meeting_transcribe", { id });
export const meetingTranscript = (id: string) => rawInvoke<Transcript>("meeting_transcript", { id });

export const meetingSpeakers = (id: string) => rawInvoke<Record<string, string>>("meeting_speakers", { id });
export const meetingSetSpeakers = (id: string, names: Record<string, string>) =>
  rawInvoke<void>("meeting_set_speakers", { id, names });
export const meetingMinutesGenerate = (id: string, templateId: string = DEFAULT_TEMPLATE_ID) =>
  rawInvoke<MinutesJson>("meeting_minutes_generate", { id, templateId });
export const meetingTemplatesList = () => rawInvoke<MinutesTemplate[]>("meeting_templates_list");
export const meetingTemplateSave = (id: string | null, name: string, body: string) =>
  rawInvoke<MinutesTemplate>("meeting_template_save", { id, name, body });
export const meetingTemplateDelete = (id: string) => rawInvoke<void>("meeting_template_delete", { id });
/** 上传 / 替换 Word / Excel 模版 (id 给了 = 替换文件); 认不出要填的空会报错。
 *  10/14: 文件先分块传到本机临时文件 (lib/uploadStage.ts), 上限 200MB */
export const meetingTemplateUpload = async (id: string | null, name: string, file: File) =>
  rawInvoke<MinutesTemplate>("meeting_template_upload", {
    id, name, filename: file.name, uploadId: await stageFile(file),
  });
/** 打开 (reveal=false) 或在文件夹里显示 (reveal=true) 按模版填好的 Word / Excel 纪要 */
export const meetingMinutesOpenFile = (id: string, reveal: boolean) =>
  rawInvoke<void>("meeting_minutes_open_file", { id, reveal });
export const meetingMinutes = (id: string) => rawInvoke<Minutes | null>("meeting_minutes", { id });

export interface AsrInstallProgress {
  step: string;
  done: boolean;
  error?: string | null;
}

export interface TranscribeProgress {
  id: string;
  phase: string;
  duration_secs?: number | null;
  done: boolean;
  error?: string | null;
}

export const onAsrInstallProgress = (cb: (p: AsrInstallProgress) => void): Promise<UnlistenFn> =>
  listen<AsrInstallProgress>("meeting_asr_progress", (e) => cb(e.payload));
export const onTranscribeProgress = (cb: (p: TranscribeProgress) => void): Promise<UnlistenFn> =>
  listen<TranscribeProgress>("meeting_transcribe_progress", (e) => cb(e.payload));

/** 说话人显示名: 改过名用名字, 没改用"说话人N" (从 1 数)。 */
export function speakerName(spk: number, names: Record<string, string>): string {
  return names[String(spk)]?.trim() || `说话人${spk + 1}`;
}

export function formatClock(secs: number): string {
  const s = Math.max(0, Math.floor(secs));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${String(m).padStart(2, "0")}:${ss}`;
}

// ── 11/12: 纪要 / 转写的修正和分享 (src-tauri commands/meeting_edit_cmd.rs) ──

export type MinutesFields = Partial<Pick<MinutesJson, "summary" | "decisions" | "action_items" | "open_questions" | "carried_over">>;
/** 改过的纪要存回去: fields = 逐项; markdown = 按文字模版写的全文 */
export const meetingMinutesSave = (id: string, fields: MinutesFields | null, markdown: string | null = null) =>
  rawInvoke<{ ok: boolean; edited_at: string }>("meeting_minutes_save", { id, fields, markdown });
/** 存成 Word (会议目录里); 用 Word / Excel 模版生成过的直接给那份 */
export const meetingMinutesDocx = (id: string) =>
  rawInvoke<{ path: string; name: string; size: number }>("meeting_minutes_docx", { id });
/** 纪要文件放进群聊暂存区, 再走 roomSend 的 files */
export const meetingMinutesStage = (id: string) =>
  rawInvoke<{ stage_id: string; name: string }>("meeting_minutes_stage", { id });
/** 改转写里的一句; 空字符串 = 改回原话。返回改后的那一段 */
export const meetingTranscriptEdit = (id: string, index: number, text: string) =>
  rawInvoke<TranscriptSegment>("meeting_transcript_edit", { id, index, text });
/** 常用热词 (改错字时记下的人名 / 术语), 新建会议时带进热词框 */
export const meetingHotwordsSaved = () => rawInvoke<string[]>("meeting_hotwords_saved");
export const meetingHotwordsRemember = (words: string[], remove = false) =>
  rawInvoke<string[]>("meeting_hotwords_remember", { words, remove });
/** 11/12 听一句: 从会议录音里截 [start, end] (前后多留一点), 返回 WAV 的 base64。track = "system" 截线上会议对方那条 */
export const meetingAudioClip = (id: string, start: number, end: number, track?: string) =>
  rawInvoke<string>("meeting_audio_clip", { id, start, end, track: track ?? null });
