/**
 * 答辩陪练 (11/18 学习第 4 步): 评审提问 (系统语音读出来, 可以重播 / 关掉)、🎤 口头回答 (本机转写) 或打字、
 * 点评 (✓ 讲到的 / ✗ 漏的 / ⚠ 说错的 + 评审点评)、追问、问完的总结。
 */
import { useEffect, useRef, useState, type CSSProperties } from "react";

import { Microphone, SpeakerHigh, Stop, Warning } from "@phosphor-icons/react";

import { Ico } from "../../components/icons";
import { speak, speechAvailable, stopSpeaking } from "../../lib/speak";
import { speechCancel, speechStart, speechStopAndTranscribe, type LearnEvent } from "../../lib/tauri_learn";
import { useVoiceSupport, voiceMissing } from "../../lib/voiceSupport";
import { inputStyle } from "../Collab/roomLinkUi";
import { ghostBtn, primaryBtn, smallBtn } from "./QuestionCard";

type Ev<K extends LearnEvent["kind"]> = Extract<LearnEvent, { kind: K }>;
const muted: CSSProperties = { fontSize: 12, color: "var(--catfish-text-muted)" };
const box: CSSProperties = {
  border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-md)", padding: "12px 16px",
  display: "flex", flexDirection: "column", gap: 8, background: "var(--catfish-bg-elevated)",
};

const VOICE_KEY = "catfish:learn:drill-voice";
export function useVoiceOn(): [boolean, (v: boolean) => void] {
  const [on, setOn] = useState(() => {
    try { return localStorage.getItem(VOICE_KEY) !== "off"; } catch { return true; }
  });
  const set = (v: boolean) => {
    setOn(v);
    try { localStorage.setItem(VOICE_KEY, v ? "on" : "off"); } catch { /* 存不了就只管这次 */ }
    if (!v) stopSpeaking();
  };
  return [on, set];
}

export function ExaminerQuestion({ ev, autoSpeak }: { ev: Ev<"examiner_q">; autoSpeak: boolean }) {
  const spoken = useRef(false);
  useEffect(() => {
    if (autoSpeak && !spoken.current) {
      spoken.current = true;
      speak(ev.text);
    }
  }, [autoSpeak, ev.text]);
  return (
    <div style={{ ...box, borderColor: "var(--catfish-cyan)" }}>
      <span style={muted}>评审{ev.followup ? " · 追问" : ev.total ? ` · 第 ${ev.qi + 1} / ${ev.total} 题` : ""}</span>
      <div style={{ fontSize: 15, lineHeight: 1.7 }}>{ev.text}</div>
      {speechAvailable() && (
        <div><button type="button" style={smallBtn} onClick={() => speak(ev.text)}><Ico as={SpeakerHigh} size={14} />再听一遍</button></div>
      )}
    </div>
  );
}

export function OralAnswer({ ev }: { ev: Ev<"oral_answer"> }) {
  return (
    <div style={{ alignSelf: "flex-end", maxWidth: "75%", background: "var(--catfish-bg-cream)", borderRadius: "var(--radius-md)",
      padding: "8px 12px", fontSize: 13, whiteSpace: "pre-wrap" }}>
      {ev.skipped ? <span style={muted}>(跳过了)</span> : ev.text}
      {ev.secs ? <div style={muted}>说了 {Math.round(ev.secs)} 秒</div> : null}
    </div>
  );
}

export function DrillEval({ ev }: { ev: Ev<"drill_eval"> }) {
  return (
    <div style={box}>
      <span style={muted}>评审对照评分点 · 讲到 {ev.covered.length} / {ev.points.length}</span>
      {ev.points.map((p, i) => {
        const ok = ev.covered.includes(i);
        return <div key={i} style={{ fontSize: 13, color: ok ? "var(--status-ok)" : "var(--status-err)" }}>{ok ? "✓" : "✗"} {p}</div>;
      })}
      {ev.wrong.map((w, i) => (
        <div key={`w${i}`} style={{ fontSize: 13, color: "var(--status-warn)" }}><Ico as={Warning} />你说「{w.said}」—— 应该是: {w.fix}</div>
      ))}
      <div style={{ fontSize: 13, marginTop: 4 }}>{ev.feedback}</div>
    </div>
  );
}

export function DrillSummary({ ev }: { ev: Ev<"drill_summary"> }) {
  const pct = ev.total ? Math.round((ev.score / ev.total) * 100) : 0;
  return (
    <div style={{ ...box, borderColor: "var(--catfish-cyan)" }}>
      <div style={{ fontWeight: 600 }}>这轮答辩问完了: 评分点讲到 {ev.score} / {ev.total} ({pct}%)</div>
      {ev.items.map((it) => (
        <div key={it.qi} style={{ fontSize: 13 }}>
          <span>{it.covered === it.total ? "✓" : "◐"} {it.question} <span style={muted}>({it.covered}/{it.total})</span></span>
          {it.missed.length > 0 && <div style={muted}>漏了: {it.missed.join("; ")}</div>}
        </div>
      ))}
      <div style={muted}>{ev.cards ? `漏掉的点做成了 ${ev.cards} 张复习卡, 明天开始复习。` : "评分点都讲到了。"} 想再来一轮, 点「答辩陪练」会重新出一套题。</div>
    </div>
  );
}

/** 回答框: 🎤 录一段 (本机转写, 填进框里能改) 或直接打字; 跳过这题。 */
export function DrillAnswerBox({ disabled, onAnswer, onSkip }: {
  disabled: boolean; onAnswer: (text: string, secs?: number) => void; onSkip: () => void;
}) {
  const [text, setText] = useState("");
  const [rec, setRec] = useState<"idle" | "recording" | "transcribing">("idle");
  const [secs, setSecs] = useState<number | undefined>(undefined);
  const [err, setErr] = useState<string | null>(null);
  const noVoiceMsg = voiceMissing("zh", useVoiceSupport());
  const started = useRef(0);
  // 离开页面时还在录 → 停掉 (不然录音器一直占着, 会议录不了); 只在卸载时看, 别在「说完了」切状态时误停
  const recRef = useRef(rec);
  recRef.current = rec;
  useEffect(() => () => { if (recRef.current === "recording") void speechCancel().catch(() => undefined); }, []);
  const toggle = async () => {
    setErr(null);
    if (rec === "idle") {
      stopSpeaking();
      try { await speechStart(); started.current = Date.now(); setRec("recording"); } catch (e) { setErr(String(e)); }
      return;
    }
    if (rec !== "recording") return;
    setRec("transcribing");
    try {
      const t = await speechStopAndTranscribe();
      setSecs((Date.now() - started.current) / 1000);
      setText((cur) => (cur ? `${cur} ${t}` : t));
    } catch (e) {
      setErr(String(e));
    } finally {
      setRec("idle");
    }
  };
  const send = () => { const t = text.trim(); if (t) { onAnswer(t, secs); setText(""); setSecs(undefined); } };
  return (
    <div style={{ ...box, borderColor: "var(--catfish-cyan)" }} aria-label="回答评审">
      <textarea aria-label="你的回答" rows={4} value={text} disabled={disabled || rec !== "idle"} onChange={(e) => setText(e.target.value)}
        placeholder="点「口头回答」用嘴说 (像现场一样), 也可以直接打字; 转出来的字可以改" style={{ ...inputStyle, resize: "vertical" }} />
      {rec === "recording" && <span style={{ fontSize: 12, color: "var(--status-err)" }}>● 录音中… 说完再点一下「说完了」</span>}
      {rec === "transcribing" && <span style={muted}>转文字中 (第一次要十几秒加载)…</span>}
      {err && <span style={{ fontSize: 12, color: "var(--status-err)" }}>{err}</span>}
      {noVoiceMsg && <span style={{ fontSize: 12, color: "var(--status-warn)" }}>{noVoiceMsg}</span>}
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", flexWrap: "wrap" }}>
        <button type="button" style={ghostBtn} disabled={disabled || rec !== "idle"} onClick={onSkip}>这题跳过</button>
        {!noVoiceMsg && (
          <button type="button" style={rec === "recording" ? { ...ghostBtn, color: "var(--status-err)", borderColor: "var(--status-err)" } : ghostBtn}
            disabled={disabled || rec === "transcribing"} onClick={() => void toggle()}>
            {rec === "recording" ? <><Ico as={Stop} />说完了</> : <><Ico as={Microphone} />口头回答</>}
          </button>
        )}
        <button type="button" style={primaryBtn(disabled || !text.trim() || rec !== "idle")} disabled={disabled || !text.trim() || rec !== "idle"}
          onClick={send}>交给评审</button>
      </div>
    </div>
  );
}
