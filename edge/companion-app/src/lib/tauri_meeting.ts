/**
 * 会议纪要 (10/1, docs/MEETING-MINUTES-PLAN.md) —— Rust 命令的前端封装。
 *
 * Rust: commands/meeting.rs (录音) / meeting_asr_cmd.rs (组件包 + 转写) /
 *       meeting_minutes_cmd.rs (说话人 + 纪要)。
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

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
  /** 10/3: 按自定义模版生成的才有; 没有 = 缺省格式 */
  template?: { id: string; name: string };
}

/** 会议纪要自定义模版 (10/3), 存在 ~/.catfish/meeting-templates/ */
export interface MinutesTemplate {
  id: string;
  name: string;
  body: string;
  updated_at: string;
}

/** 缺省模版 = 原来那份固定格式, 不落盘、不能改 */
export const DEFAULT_TEMPLATE_ID = "default";

export interface Minutes {
  json: MinutesJson;
  markdown: string;
  path: string;
}

export const meetingList = () => rawInvoke<MeetingMeta[]>("meeting_list");
export const meetingCreate = (title: string, attendees: number, hotwords: string[]) =>
  rawInvoke<MeetingMeta>("meeting_create", { title, attendees, hotwords });
export const meetingAudioDevices = () => rawInvoke<InputDevice[]>("meeting_audio_devices");
export const meetingRecordStart = (id: string, device: string | null) =>
  rawInvoke<RecorderStatus>("meeting_record_start", { id, device });
export const meetingRecordStatus = () => rawInvoke<RecorderStatus>("meeting_record_status");
export const meetingRecordStop = () => rawInvoke<MeetingMeta | null>("meeting_record_stop");

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
