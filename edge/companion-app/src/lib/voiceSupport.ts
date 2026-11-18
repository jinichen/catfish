/**
 * 这台电脑上 🎤 能转哪些语言 (11/18 学习外语): 按实际装了哪个组件说, 不写死。
 *   中文 / 英语 → 默认的会议组件 (FunASR; 英语实测一字不差)
 *   日语 / 韩语 / 法语 … → 高精度转写组件 (Qwen3-ASR, meeting-asr-hq; 装它要先有会议组件)
 * 状态来自 meeting_hq_status (同时报默认组件装没装); 每次用到的页面挂上来都重读, 先用上次读到的。
 */
import { useEffect, useState } from "react";

import { meetingHqStatus } from "./tauri_meeting";

export interface VoiceSupport { base: boolean; hq: boolean }

let cache: VoiceSupport | null = null;
let inflight: Promise<VoiceSupport> | null = null;
const listeners = new Set<(v: VoiceSupport) => void>();

export function refreshVoiceSupport(): Promise<VoiceSupport> {
  inflight = meetingHqStatus()
    .then((s) => ({ base: !!s.base_installed, hq: !!s.installed }))
    .catch(() => ({ base: false, hq: false }))
    .then((v) => { cache = v; listeners.forEach((f) => f(v)); inflight = null; return v; });
  return inflight;
}

export function _resetVoiceSupport() { cache = null; inflight = null; }

/** null = 还没读到 */
export function useVoiceSupport(): VoiceSupport | null {
  const [v, setV] = useState<VoiceSupport | null>(cache);
  useEffect(() => {
    listeners.add(setV);
    // 每次进来都重读一次 (很便宜): 员工可能刚在会议页装完组件, 别让学习页还说没装
    if (!inflight) void refreshVoiceSupport();
    return () => { listeners.delete(setV); };
  }, []);
  return v;
}

const NEEDS_BASE = new Set(["zh", "en"]);

/** 这种语言 🎤 用不了时给员工看的话; 能用 (或还没读到状态) 返回 null */
export function voiceMissing(code: string, v: VoiceSupport | null): string | null {
  if (!v) return null;
  if (NEEDS_BASE.has(code)) return v.base ? null : "🎤 要先在「会议」页装会议组件 (一次就好), 现在可以先打字";
  if (v.hq) return null;
  return v.base
    ? "🎤 这种语言要先在「会议」页装「高精度转写组件」(选一场会点「高精度重新转写」按提示装), 现在可以先打字"
    : "🎤 这种语言要先在「会议」页装会议组件, 再装「高精度转写组件」; 现在可以先打字";
}

/** 下拉框里的短标注 */
export function voiceTag(code: string, v: VoiceSupport | null): string {
  if (!v || !voiceMissing(code, v)) return "";
  return NEEDS_BASE.has(code) ? " (🎤 要会议组件)" : " (🎤 要高精度组件)";
}
