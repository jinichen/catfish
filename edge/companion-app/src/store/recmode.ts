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
  /** 5/14 G: 错类型分类, 给 UI 显不同 hint + 修法引导 */
  errorCategory:
    | "cdp_unavailable"     // CDP ws 连不上 (Catfish Chrome 没起 / 端口错)
    | "whisper_failed"       // ffmpeg / whisper.cpp 跑挂
    | "aggregator_timeout"   // LLM 综合超时
    | "llm_parse_failed"     // LLM 输出 JSON parse 错
    | "network"              // gateway / network 通用错
    | "unknown"              // 兜底
    | null;

  // ── actions ──
  openSetup: () => void;
  closeSetup: () => void;
  setSetup: (s: Partial<RecModeSetup>) => void;
  startRecording: (sessionId: string) => void;
  stopRecording: () => void;
  startAnalyzing: () => void;
  showPreview: (p: RecModeSkillPreview) => void;
  setError: (msg: string, category?: RecModeStateData["errorCategory"]) => void;
  reset: () => void;
}

/** Helper: 按错误消息内容自动分类 errorCategory.
 *
 * RecMode 的几类典型错: CDP ws 连不上 / whisper 挂 / aggregator timeout /
 * LLM JSON parse fail / 通用网络. UI 根据分类给不同修法引导.
 */
export function classifyRecModeError(msg: string): RecModeStateData["errorCategory"] {
  const m = msg.toLowerCase();
  if (m.includes("ws") || m.includes("cdp") || m.includes("9222") || m.includes("chrome")) {
    return "cdp_unavailable";
  }
  if (m.includes("whisper") || m.includes("ffmpeg") || m.includes("speech_") || m.includes("audio")) {
    return "whisper_failed";
  }
  if (m.includes("timeout") || m.includes("超时")) {
    return "aggregator_timeout";
  }
  if (m.includes("parse") || m.includes("json") || m.includes("找不到")) {
    return "llm_parse_failed";
  }
  if (m.includes("network") || m.includes("fetch") || m.includes("connection") || m.includes("不可达")) {
    return "network";
  }
  return "unknown";
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
  errorCategory: null,

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
  setError: (errorMessage, category) =>
    set({
      state: "error",
      errorMessage,
      errorCategory: category ?? classifyRecModeError(errorMessage),
    }),
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
      errorCategory: null,
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

/** Helper: snake_case 校验 (catfish skill 范式) — 给后端 / LLM 综合用, 不暴露 UI */
export function isValidSkillName(name: string): boolean {
  return /^[a-z][a-z0-9_]*$/.test(name) && name.length >= 3 && name.length <= 60;
}

/** Helper: 人话标题校验 (5/14 鸿波"UI 不是产品水平" 反馈后加).
 *
 * 用户输人话 (中文 / 英文 / 混), 后端 LLM aggregator 综合时自动起 snake_case
 * skill_name. 前端只校验 title 不过短 / 不过长 / 不全空白.
 *
 * 不暴露 snake_case 给普通员工 (HR / 财务 / 销售 看到术语就不学了, 跟 catfish
 * 产品定位 "非开发者 AI 平台" 反着走).
 */
export function isValidSkillTitle(title: string): boolean {
  const t = title.trim();
  return t.length >= 3 && t.length <= 100;
}

/** 给 setup 模态的 "示例" 一键 prefill, 让用户秒懂 RecMode 适合啥场景 */
export const RECMODE_EXAMPLES: { title: string; description: string }[] = [
  {
    title: "检查 EIS 资质过期",
    description: "EIS 周一上午查企业资质快过期的, 90 天内到期的标记出来续期",
  },
  {
    title: "周报数据汇总",
    description: "每周五下午从 OA 拉本部门数据 → 生成 markdown 周报",
  },
  {
    title: "新员工 onboarding",
    description: "HR 新员工入职流程: 创建账号 / 加飞书群 / 发欢迎邮件",
  },
];
