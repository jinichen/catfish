/** RecMode RecordingOverlay — 抽自 RecModeButton.tsx (5/20 拆分).
 *
 * 录制中 / analyzing 浮层. 显计时 + 暂停按钮 + ✅完成 / ⏸ 中止.
 */

import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import {
  useRecModeStore,
  formatRecordingElapsed,
} from "../../../store/recmode";
import {
  stopRecording as apiStopRecording,
  analyzeRecording,
  recordTranscript,
  getRecordingStatus,
  type RecordingStatus,
} from "../../../lib/recmode";

import { OverlayShell, btnStyle } from "./shared";


function RecordingOverlay() {
  const state = useRecModeStore((s) => s.state);
  const sessionId = useRecModeStore((s) => s.sessionId);
  const startedAt = useRecModeStore((s) => s.startedAt);
  const stopRecording = useRecModeStore((s) => s.stopRecording);
  const showPreview = useRecModeStore((s) => s.showPreview);
  const setError = useRecModeStore((s) => s.setError);
  const reset = useRecModeStore((s) => s.reset);
  const [submitting, setSubmitting] = useState(false);
  const [, setTick] = useState(0);
  const [liveStatus, setLiveStatus] = useState<RecordingStatus | null>(null);

  // 1 秒一刷计时
  useEffect(() => {
    if (state !== "recording") return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [state]);

  // F: 每 2s polling /api/learn/status 拉实时 keyframes / events 计数
  useEffect(() => {
    if (state !== "recording" || !sessionId) return;
    let cancelled = false;
    async function poll() {
      try {
        const s = await getRecordingStatus(sessionId!);
        if (!cancelled) setLiveStatus(s);
      } catch {
        // session 没了 / 网络挂 — 静默, 用户用 stop 手动收尾
      }
    }
    void poll();
    const t = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [state, sessionId]);

  // V2 #70: 拿录音 toggle 状态 (录屏开始时 setup.recordAudio 已落)
  const isRecordingAudio = useRecModeStore((s) => s.isRecordingAudio);

  async function onFinish() {
    if (!sessionId || submitting) return;
    setSubmitting(true);
    try {
      // V2 #70: 没开录音就跳过 whisper (start 时也没起 ffmpeg)
      if (isRecordingAudio) {
        try {
          const transcript = await invoke<string>("speech_stop_and_transcribe");
          if (transcript && transcript.trim()) {
            const elapsedAtStop = startedAt
              ? Math.floor(Date.now() / 1000) - startedAt
              : 0;
            await recordTranscript(sessionId, transcript, {
              tsOffset: 0,
              duration: elapsedAtStop,
            });
          }
        } catch (e) {
          console.warn("[recmode] speech_stop / record_transcript 失败 (继续, 没语音):", e);
        }
      }
      // 停 CDP listener (flush events.jsonl + meta.json)
      await apiStopRecording(sessionId);
      // 触发 analyze (调 main 综合 → 落 draft skill)
      stopRecording();  // state → analyzing
      const preview = await analyzeRecording(sessionId);
      showPreview(preview);
    } catch (e) {
      setError(`完成教学失败: ${(e as Error).message || e}`);
    } finally {
      setSubmitting(false);
    }
  }

  async function onAbort() {
    if (!sessionId) return;
    try {
      await invoke("speech_stop_and_transcribe").catch(() => {});
      await apiStopRecording(sessionId).catch(() => {});
    } finally {
      reset();
    }
  }

  return (
    <OverlayShell>
      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: "var(--space-2)" }}>
        {state === "recording" ? "🔴 录屏中" : "⏳ 分析中..."}
      </div>
      {state === "recording" && (
        <>
          <div style={{ fontSize: 12, color: "var(--catfish-text-muted)", marginBottom: "var(--space-3)" }}>
            ⏱ {formatRecordingElapsed(startedAt)}
            {liveStatus && <> · 📸 {liveStatus.keyframes_count} keyframes · 📊 {liveStatus.events_count} events</>}
            {liveStatus && !liveStatus.ws_connected && (
              <span style={{ color: "var(--status-warn)" }}> · ⚠ ws 未连</span>
            )}
          </div>
          <div style={{ fontSize: 13, lineHeight: 1.5, marginBottom: "var(--space-3)" }}>
            现在切到 <strong>Catfish Chrome</strong> 正常操作.
            <br />
            <strong>顺嘴说</strong> 你在干嘛 (鲶鱼能听懂):
            <br />
            <span style={{ color: "var(--catfish-text-muted)", fontSize: 12 }}>
              "现在点应用是为了进资质管理"
              <br />
              "看这个证书有效期, 90 天内的就要标"
            </span>
          </div>
          <div style={{ display: "flex", gap: "var(--space-2)" }}>
            <button onClick={onFinish} disabled={submitting} style={btnStyle("primary", submitting)}>
              {submitting ? "处理中..." : "✅ 完成教学"}
            </button>
            <button onClick={onAbort} disabled={submitting} style={btnStyle("secondary", submitting)}>
              ❌ 取消放弃
            </button>
          </div>
        </>
      )}
      {state === "analyzing" && (
        <div style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>
          鲶鱼后端 (catfish-private-main) 正在综合你刚录的 events + 截图 + 语音.
          <br />
          这一步通常 30-90 秒, 取决于录屏长度.
        </div>
      )}
    </OverlayShell>
  );
}

export default RecordingOverlay;
