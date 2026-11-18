/**
 * 外语练习 (11/18 学习第 5 步): 情景对话 (小鲶扮角色, 每轮纠错) + 跟读 (系统语音读、你读, 逐词对照) + 复盘做卡。
 * 朗读用系统自带的对应语言的声音 (不联网); 🎤 转写: 英语用会议组件, 日语等要高精度转写组件。
 * 跟读对照只看「读出来的字对不对得上」, 不是口音打分。
 */
import { useEffect, useRef, useState, type CSSProperties } from "react";

import { speak, speechAvailable, stopSpeaking } from "../../lib/speak";
import {
  LANG_BCP47, LANG_NAMES, speechCancel, speechStart, speechStopAndTranscribeLang, type LearnEvent,
} from "../../lib/tauri_learn";
import { inputStyle } from "../Collab/roomLinkUi";
import { ghostBtn, primaryBtn } from "./QuestionCard";

type Ev<K extends LearnEvent["kind"]> = Extract<LearnEvent, { kind: K }>;
const muted: CSSProperties = { fontSize: 12, color: "var(--catfish-text-muted)" };
const box: CSSProperties = {
  border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-md)", padding: "12px 14px",
  display: "flex", flexDirection: "column", gap: 6, background: "var(--catfish-bg-elevated)",
};
const small: CSSProperties = { ...ghostBtn, fontSize: 12, padding: "2px 8px" };
/** 日语 / 韩语 / 中文按字对照 (后端 learning_lang.tokens 同口径) */
const CJK = new Set(["ja", "ko", "zh"]);

/** 🎤 录一段 → 按语言转文字 */
export function useRecorder(lang: string) {
  const [state, setState] = useState<"idle" | "recording" | "transcribing">("idle");
  const [err, setErr] = useState<string | null>(null);
  const started = useRef(0);
  const ref = useRef(state);
  ref.current = state;
  useEffect(() => () => { if (ref.current === "recording") void speechCancel().catch(() => undefined); }, []);
  const start = async () => {
    setErr(null);
    stopSpeaking();
    try { await speechStart(); started.current = Date.now(); setState("recording"); } catch (e) { setErr(String(e)); }
  };
  const stop = async (): Promise<{ text: string; secs: number } | null> => {
    setState("transcribing");
    try {
      const text = await speechStopAndTranscribeLang(lang);
      return { text, secs: (Date.now() - started.current) / 1000 };
    } catch (e) {
      setErr(String(e));
      return null;
    } finally {
      setState("idle");
    }
  };
  return { state, err, start, stop };
}

export function LangStart({ code, disabled, onStart }: { code: string; disabled: boolean; onStart: (activity: "dialogue" | "shadow", scene: string) => void }) {
  const [scene, setScene] = useState("");
  return (
    <div style={{ ...box, borderColor: "var(--catfish-cyan)" }} aria-label="选练法">
      <div style={{ fontWeight: 600 }}>练{LANG_NAMES[code] ?? code}: 选一种</div>
      <input aria-label="情景" value={scene} onChange={(e) => setScene(e.target.value)} disabled={disabled}
        placeholder="情景 (可选), 例: 跟海外客户确认交付时间" style={inputStyle} />
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button type="button" style={primaryBtn(disabled)} disabled={disabled} onClick={() => onStart("dialogue", scene)}>💬 情景对话</button>
        <button type="button" style={ghostBtn} disabled={disabled} onClick={() => onStart("shadow", scene)}>🔁 跟读</button>
      </div>
      <span style={muted}>对话: 小鲶扮角色跟你聊, 每句帮你改; 跟读: 小鲶读一句你读一句, 对照哪些词没读出来。都用🎤说, 也可以打字。</span>
    </div>
  );
}

export function LangScene({ ev }: { ev: Ev<"lang_scene"> }) {
  return (
    <div style={{ ...muted, borderTop: "1px solid var(--catfish-border)", paddingTop: 8 }}>
      {ev.activity === "dialogue" ? "💬 情景对话" : "🔁 跟读"}: {ev.setting}{ev.role_name ? ` · 小鲶扮: ${ev.role_name}` : ""}
    </div>
  );
}

export function LangLine({ ev, code, autoSpeak }: { ev: Ev<"lang_line">; code: string; autoSpeak: boolean }) {
  const [zh, setZh] = useState(false);
  const spoken = useRef(false);
  const bcp = LANG_BCP47[code] ?? "en-US";
  useEffect(() => {
    if (autoSpeak && ev.who === "role" && !spoken.current) { spoken.current = true; speak(ev.text, 0.95, bcp); }
  }, [autoSpeak, ev, bcp]);
  if (ev.who === "learner") {
    return (
      <div style={{ alignSelf: "flex-end", maxWidth: "75%", background: "var(--catfish-bg-cream)", borderRadius: "var(--radius-md)",
        padding: "8px 12px", fontSize: 14, whiteSpace: "pre-wrap" }}>
        {ev.text}{ev.secs ? <div style={muted}>🎤 {Math.round(ev.secs)} 秒</div> : null}
      </div>
    );
  }
  return (
    <div style={{ ...box, maxWidth: "85%" }}>
      <div style={{ fontSize: 15, lineHeight: 1.7 }}>{ev.text}</div>
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
        {speechAvailable() && <button type="button" style={small} onClick={() => speak(ev.text, 0.95, bcp)}>🔊 听</button>}
        {speechAvailable() && <button type="button" style={small} onClick={() => speak(ev.text, 0.7, bcp)}>🐢 慢点</button>}
        {ev.zh && <button type="button" style={small} onClick={() => setZh(!zh)}>{zh ? "收起中文" : "看中文"}</button>}
      </div>
      {zh && ev.zh && <div style={muted}>{ev.zh}</div>}
    </div>
  );
}

export function LangFix({ ev, code }: { ev: Ev<"lang_fix">; code: string }) {
  return (
    <div style={{ fontSize: 13, borderLeft: "3px solid var(--status-warn)", padding: "4px 10px", display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={muted}>✏️ 可以这样说</span>
      {ev.items.map((x, i) => (
        <div key={i}>
          {x.said && <span style={{ textDecoration: "line-through", color: "var(--catfish-text-muted)" }}>{x.said}</span>}
          {x.said && " → "}<b>{x.better}</b>
          {speechAvailable() && <button type="button" style={{ ...small, marginLeft: 6 }} onClick={() => speak(x.better, 0.9, LANG_BCP47[code] ?? "en-US")}>🔊</button>}
          {x.why && <div style={muted}>{x.why}</div>}
        </div>
      ))}
    </div>
  );
}

export function LangSay({ code, disabled, onSay, onEnd }: { code: string; disabled: boolean; onSay: (text: string, secs?: number) => void; onEnd: () => void }) {
  const [text, setText] = useState("");
  const [secs, setSecs] = useState<number | undefined>();
  const rec = useRecorder(code);
  const toggle = async () => {
    if (rec.state === "idle") { await rec.start(); return; }
    const r = await rec.stop();
    if (r) { setText((c) => (c ? `${c} ${r.text}` : r.text)); setSecs(r.secs); }
  };
  const send = () => { const t = text.trim(); if (t) { onSay(t, secs); setText(""); setSecs(undefined); } };
  return (
    <div style={{ ...box, borderColor: "var(--catfish-cyan)" }} aria-label="外语回答">
      <textarea aria-label="用外语说" rows={3} value={text} disabled={disabled || rec.state !== "idle"} onChange={(e) => setText(e.target.value)}
        placeholder={`用${LANG_NAMES[code] ?? "外语"}回答 (点🎤说; 不会说就写中文, 小鲶教你怎么说)`} style={{ ...inputStyle, resize: "vertical" }} />
      {rec.state === "recording" && <span style={{ fontSize: 12, color: "var(--status-err)" }}>● 录音中… 说完再点「说完了」</span>}
      {rec.state === "transcribing" && <span style={muted}>转文字中…</span>}
      {rec.err && <span style={{ fontSize: 12, color: "var(--status-err)" }}>{rec.err}</span>}
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", flexWrap: "wrap" }}>
        <button type="button" style={ghostBtn} disabled={disabled || rec.state !== "idle"} onClick={onEnd}>结束这段, 复盘</button>
        <button type="button" style={rec.state === "recording" ? { ...ghostBtn, color: "var(--status-err)", borderColor: "var(--status-err)" } : ghostBtn}
          disabled={disabled || rec.state === "transcribing"} onClick={() => void toggle()}>{rec.state === "recording" ? "⏹ 说完了" : "🎤 说"}</button>
        <button type="button" style={primaryBtn(disabled || !text.trim() || rec.state !== "idle")} disabled={disabled || !text.trim() || rec.state !== "idle"}
          onClick={send}>发送</button>
      </div>
    </div>
  );
}

/** 跟读: 每句 🔊 听 / 🎤 读; 最近一次的对照 —— 原句里没对上的词标红 */
export function ShadowSet({ ev, code, results, disabled, onRead, onEnd }: {
  ev: Ev<"shadow_set">; code: string; results: Map<number, Ev<"shadow_result">>; disabled: boolean;
  onRead: (idx: number, heard: string, secs: number) => void; onEnd: () => void;
}) {
  const bcp = LANG_BCP47[code] ?? "en-US";
  const rec = useRecorder(code);
  const [active, setActive] = useState<number | null>(null);
  const toggle = async (i: number) => {
    if (rec.state === "idle") { setActive(i); await rec.start(); return; }
    if (active !== i) return;
    const r = await rec.stop();
    setActive(null);
    if (r) onRead(i, r.text, r.secs);
  };
  const done = [...results.values()];
  const avg = done.length ? Math.round((done.reduce((n, r) => n + r.score, 0) / done.length) * 100) : null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {ev.sentences.map((s, i) => {
        const r = results.get(i);
        const missed = new Set(r?.missed ?? []);
        return (
          <div key={i} style={box}>
            <div style={{ fontSize: 15, lineHeight: 1.7 }}>
              {r ? (CJK.has(code) ? Array.from(s.text) : s.text.split(/(\s+)/)).map((w, j) => {
                const key = w.toLowerCase().replace(/[^\p{L}\p{N}']/gu, "");
                return <span key={j} style={key && missed.has(key) ? { color: "var(--status-err)", textDecoration: "underline" } : undefined}>{w}</span>;
              }) : s.text}
            </div>
            <div style={muted}>{s.zh}{s.note ? ` · 💡 ${s.note}` : ""}</div>
            <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
              {speechAvailable() && <button type="button" style={small} onClick={() => speak(s.text, 0.9, bcp)}>🔊 听</button>}
              {speechAvailable() && <button type="button" style={small} onClick={() => speak(s.text, 0.65, bcp)}>🐢 慢点</button>}
              <button type="button" disabled={disabled || rec.state === "transcribing" || (rec.state === "recording" && active !== i)}
                style={rec.state === "recording" && active === i ? { ...small, color: "var(--status-err)", borderColor: "var(--status-err)" } : small}
                onClick={() => void toggle(i)}>{rec.state === "recording" && active === i ? "⏹ 读完了" : rec.state === "transcribing" && active === i ? "对照中…" : "🎤 跟读"}</button>
              {r && (
                <span style={{ fontSize: 12, color: r.score >= 0.9 ? "var(--status-ok)" : r.score >= 0.6 ? "var(--status-warn)" : "var(--status-err)" }}>
                  对上 {Math.round(r.score * 100)}%{r.missed.length ? ` · 没听出来: ${r.missed.join(" ")}` : ""}
                </span>
              )}
            </div>
            {r && <div style={muted}>识别成: {r.heard}</div>}
          </div>
        );
      })}
      {rec.err && <span style={{ fontSize: 12, color: "var(--status-err)" }}>{rec.err}</span>}
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button type="button" style={primaryBtn(disabled || done.length === 0)} disabled={disabled || done.length === 0} onClick={onEnd}>读完了, 复盘</button>
        <span style={muted}>{avg !== null ? `读了 ${done.length} 句, 平均对上 ${avg}%。` : ""}标红的是没识别出来的词 (读错 / 漏读 / 太快); 这是对字, 不是口音打分。</span>
      </div>
    </div>
  );
}

export function LangRecap({ ev, code }: { ev: Ev<"lang_recap">; code: string }) {
  const bcp = LANG_BCP47[code] ?? "en-US";
  return (
    <div style={{ ...box, borderColor: "var(--catfish-cyan)" }}>
      <div style={{ fontWeight: 600 }}>📝 复盘</div>
      <div style={{ fontSize: 13 }}>{ev.summary}</div>
      {ev.expressions.length > 0 && <span style={muted}>值得记住的说法</span>}
      {ev.expressions.map((x, i) => (
        <div key={i} style={{ fontSize: 13 }}>
          <b>{x.text}</b> <span style={muted}>{x.zh}</span>
          {speechAvailable() && <button type="button" style={{ ...small, marginLeft: 6 }} onClick={() => speak(x.text, 0.9, bcp)}>🔊</button>}
        </div>
      ))}
      {ev.mistakes.length > 0 && <span style={muted}>要紧的错</span>}
      {ev.mistakes.map((x, i) => (
        <div key={`m${i}`} style={{ fontSize: 13 }}>
          <span style={{ textDecoration: "line-through", color: "var(--catfish-text-muted)" }}>{x.said}</span> → <b>{x.better}</b>
          <div style={muted}>{x.why}</div>
        </div>
      ))}
      <div style={muted}>{ev.cards ? `做了 ${ev.cards} 张复习卡, 明天开始复习。` : ""} 想再练, 上面再选一种。</div>
    </div>
  );
}
