/**
 * 会议页「听一句」(11/12 鸿波「不能播放录音, 怎么来修正识别内容」): 一次只放一句, 点别的就换; 再点同一句 = 停。
 * Rust 截好的 WAV (base64) → blob → <audio>。只在本机放, 录音不出本机。
 */
import { meetingAudioClip, type TranscriptSegment } from "../../lib/tauri_meeting";

let current: { key: string; audio: HTMLAudioElement; url: string } | null = null;
const listeners = new Set<(key: string | null) => void>();

function emit(key: string | null) {
  listeners.forEach((f) => f(key));
}

export function playingKey(): string | null {
  return current?.key ?? null;
}

export function onPlayingChange(f: (key: string | null) => void): () => void {
  listeners.add(f);
  return () => listeners.delete(f);
}

export function stopClip() {
  if (!current) return;
  current.audio.pause();
  URL.revokeObjectURL(current.url);
  current = null;
  emit(null);
}

/** 放一句; 正在放的就是这句 → 停。key 用来让界面标出「正在放哪句」 */
export async function toggleClip(key: string, meetingId: string, seg: Pick<TranscriptSegment, "start" | "end" | "track">): Promise<void> {
  if (current?.key === key) {
    stopClip();
    return;
  }
  stopClip();
  const b64 = await meetingAudioClip(meetingId, seg.start, seg.end, seg.track);
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
  const audio = new Audio(url);
  current = { key, audio, url };
  audio.onended = () => { if (current?.key === key) stopClip(); };
  emit(key);
  await audio.play();
}

/** 认说话人用: 这个人说得最长、但不超过 15 秒的一句 */
export function sampleSegment(segments: TranscriptSegment[], spk: number): TranscriptSegment | null {
  const mine = segments.filter((s) => s.spk === spk);
  if (!mine.length) return null;
  const ok = mine.filter((s) => s.end - s.start <= 15);
  const pool = ok.length ? ok : mine;
  return pool.reduce((a, b) => (b.end - b.start > a.end - a.start ? b : a));
}
