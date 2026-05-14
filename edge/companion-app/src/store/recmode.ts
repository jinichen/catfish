/** BL-LEARN-RECMODE Companion 状态机 (5/14 Day 2 #64).
 *
 * 录屏+语音教学引擎的客户端状态:
 *   idle       — 没在录, 默认状态
 *   setup      — 用户点 🎙 后填名字 / namespace / 简述
 *   recording  — gateway 启了 CDP listener + ffmpeg 在录音, 用户操作 Catfish Chrome
 *   analyzing  — 用户点 ✅ 完成, gateway 跑 aggregator (调 main 综合)
 *   preview    — analyze done, 显 SKILL.md 草稿 + "跑一次试" / "保存" / "重录" 三按钮
 *   error      — 任何环节挂, 显友好错误 + "重试" / "放弃" 按钮
 *
 * 数据从 /api/learn/* endpoints 拉:
 *   start_recording → recording
 *   stop_recording → analyzing (然后立刻 analyze)
 *   analyze → preview (拿 skill_dir + steps_count + confidence + questions)
 *
 * 跟 chat store 解耦 — RecMode 跟 Chat 是两个独立 tab, 不共享 zustand state.
 * 但 chat input toolbar 的 🎙 按钮触发 setup → setup 弹模态后切到 RecMode 主流程.
 */

import { create } from "zustand";

export type RecModeState =
  | "idle"
  | "setup"
  | "recording"
  | "analyzing"
  | "preview"
  | "error";

export interface RecModeSetup {
  /** snake_case skill name, e.g. eis_qualification_check */
  name: string;
  /** 'department' / 'personal' / 'public' */
  namespace: string;
  /** 简述 (1-2 句, 可选) */
  description: string;
}

export interface RecModeSkillPreview {
  skill_name: string;
  namespace: string;
  skill_dir: string;
  steps_count: number;
  confidence: number;
  questions_for_user: string[];
}

interface RecModeStateData {
  state: RecModeState;
  /** 当前 session_id (start_recording 时生成) */
  sessionId: string | null;
  /** 录制开始时间 (epoch sec), 用于显示 "⏱ 0:34" */
  startedAt: number | null;
  /** keyframe 计数 — 录中实时更新 (5/26 加: SSE / poll /api/learn/active) */
  keyframesCount: number;
  /** 录中是否同时开了 ffmpeg 录音 (跟 BL-VOICE3 复用) */
  isRecordingAudio: boolean;
  /** setup 表单内容 */
  setup: RecModeSetup;
  /** analyze 完成的 skill 信息 */
  preview: RecModeSkillPreview | null;
  /** 友好错误消息 (state=error 时) */
  errorMessage: string | null;

  // ── actions ──
  openSetup: () => void;
  closeSetup: () => void;
  setSetup: (s: Partial<RecModeSetup>) => void;
  startRecording: (sessionId: string) => void;
  stopRecording: () => void;
  startAnalyzing: () => void;
  showPreview: (p: RecModeSkillPreview) => void;
  setError: (msg: string) => void;
  reset: () => void;
}

const DEFAULT_SETUP: RecModeSetup = {
  name: "",
  namespace: "personal",
  description: "",
};

export const useRecModeStore = create<RecModeStateData>((set) => ({
  state: "idle",
  sessionId: null,
  startedAt: null,
  keyframesCount: 0,
  isRecordingAudio: false,
  setup: { ...DEFAULT_SETUP },
  preview: null,
  errorMessage: null,

  openSetup: () => set({ state: "setup" }),
  closeSetup: () => set({ state: "idle" }),
  setSetup: (s) =>
    set((cur) => ({ setup: { ...cur.setup, ...s } })),
  startRecording: (sessionId) =>
    set({
      state: "recording",
      sessionId,
      startedAt: Math.floor(Date.now() / 1000),
      keyframesCount: 0,
      isRecordingAudio: true,
      errorMessage: null,
    }),
  stopRecording: () => set({ state: "analyzing" }),
  startAnalyzing: () => set({ state: "analyzing" }),
  showPreview: (preview) =>
    set({
      state: "preview",
      preview,
      errorMessage: null,
    }),
  setError: (errorMessage) =>
    set({ state: "error", errorMessage }),
  reset: () =>
    set({
      state: "idle",
      sessionId: null,
      startedAt: null,
      keyframesCount: 0,
      isRecordingAudio: false,
      setup: { ...DEFAULT_SETUP },
      preview: null,
      errorMessage: null,
    }),
}));

/** Helper: 格式化录制时长 (epoch sec → 'M:SS') 给状态卡显示 */
export function formatRecordingElapsed(startedAt: number | null): string {
  if (!startedAt) return "0:00";
  const now = Math.floor(Date.now() / 1000);
  const sec = Math.max(0, now - startedAt);
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/** Helper: snake_case 校验 (RecMode 名字必 snake_case 跟 catfish skill 范式对齐) */
export function isValidSkillName(name: string): boolean {
  return /^[a-z][a-z0-9_]*$/.test(name) && name.length >= 3 && name.length <= 60;
}
