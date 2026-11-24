/**
 * 学习里的题 (11/18): 没答的是可以作答的卡, 答过的显示对错、标准答案和解析。
 * 每题都能填「把握」(0–100%) 和「你怎么想的」—— 小鲶靠这两样判断是真会、蒙对、还是有个确信的误解 (docs/LEARNING-PLAN.md §2)。
 * 草稿存在模块内存里 (切 tab 回来还在)。
 */
import { useState, type CSSProperties } from "react";

import { Markdown } from "../../lib/markdown";
import type { LearnAnswer, LearnEvent, LearnQuestion } from "../../lib/tauri_learn";
import { inputStyle } from "../Collab/roomLinkUi";

/** 题目里单个换行照原样换行 —— 模型常把 a) b) c) 选项用单个换行分开写在题干里, Markdown 会把它们挤成一段 (11/18 渲染时看到) */
export const hardBreaks = (t: string) => t.replace(/([^\n])\n(?!\n)/g, "$1  \n");

type AnswerEv = Extract<LearnEvent, { kind: "answer" }>;
type GradeEv = Extract<LearnEvent, { kind: "grade" }>;

const ROLE_LABEL: Record<LearnQuestion["role"], string> = {
  probe: "摸底", predict: "先猜一猜 (还没讲, 猜错没关系)", check: "检查一下", ask: "问你",
};
const VERDICT: Record<string, { text: string; color: string }> = {
  correct: { text: "✓ 对", color: "var(--status-ok)" },
  wrong: { text: "✗ 不对", color: "var(--status-err)" },
  partial: { text: "◐ 部分对", color: "var(--status-warn)" },
  dont_know: { text: "不知道", color: "var(--catfish-text-muted)" },
};

const box: CSSProperties = {
  border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-md)", padding: "12px 16px",
  display: "flex", flexDirection: "column", gap: 8, background: "var(--catfish-bg-elevated)",
};
const muted: CSSProperties = { fontSize: 12, color: "var(--catfish-text-muted)" };

type Draft = { picked: string[]; text: string; confidence: number | null; thinking: string };
const drafts = new Map<string, Draft>();
export const _clearDrafts = () => drafts.clear();

export function OpenQuestion({ qid, q, disabled, onSubmit }: {
  qid: string; q: LearnQuestion; disabled: boolean; onSubmit: (a: LearnAnswer) => void;
}) {
  const [d, setD] = useState<Draft>(() => drafts.get(qid) ?? { picked: [], text: "", confidence: null, thinking: "" });
  const put = (p: Partial<Draft>) => setD((cur) => { const n = { ...cur, ...p }; drafts.set(qid, n); return n; });
  const graded = q.type !== "ask";
  const toggle = (v: string) => put({ picked: q.multi ? (d.picked.includes(v) ? d.picked.filter((x) => x !== v) : [...d.picked, v]) : [v] });
  const ready = q.type === "recall" ? d.text.trim() !== "" : q.type === "choice" ? d.picked.length > 0 : d.picked.length > 0 || d.text.trim() !== "";
  const send = (dontKnow = false) => {
    const a: LearnAnswer = { qid, thinking: d.thinking.trim() || undefined };
    if (dontKnow) a.dont_know = true;
    else {
      if (q.type !== "recall") a.picked = d.picked;
      if (d.text.trim()) a.text = d.text.trim();
      if (graded && d.confidence !== null) a.confidence = d.confidence;
    }
    drafts.delete(qid);
    onSubmit(a);
  };
  return (
    <div style={{ ...box, borderColor: "var(--catfish-cyan)" }} aria-label="当前的题">
      <span style={muted}>{ROLE_LABEL[q.role]}{q.multi ? " · 多选" : ""}</span>
      <Markdown text={hardBreaks(q.question)} />
      {(q.options ?? []).map((o) => (
        <label key={o.value} style={{ display: "flex", gap: 8, alignItems: "flex-start", cursor: "pointer", fontSize: 13 }}>
          <input type={q.multi ? "checkbox" : "radio"} name={qid} checked={d.picked.includes(o.value)} disabled={disabled}
            onChange={() => toggle(o.value)} style={{ marginTop: 3 }} />
          <span><b>{o.value}.</b> {o.label}</span>
        </label>
      ))}
      {q.type !== "choice" && (
        <textarea aria-label="你的回答" rows={q.type === "recall" ? 4 : 2} value={d.text} disabled={disabled}
          placeholder={q.type === "recall" ? "用你自己的话答" : "也可以自己写"} onChange={(e) => put({ text: e.target.value })}
          style={{ ...inputStyle, resize: "vertical" }} />
      )}
      {graded && (
        <label style={{ ...muted, display: "flex", alignItems: "center", gap: 8 }}>
          有几成把握
          <input type="range" min={0} max={100} step={10} aria-label="把握" value={d.confidence ?? 50} disabled={disabled}
            onChange={(e) => put({ confidence: Number(e.target.value) })} style={{ flex: 1, maxWidth: 220 }} />
          <span style={{ width: 36 }}>{d.confidence === null ? "—" : `${d.confidence}%`}</span>
        </label>
      )}
      <textarea aria-label="你怎么想的" rows={2} value={d.thinking} disabled={disabled}
        placeholder="你怎么想的 (可选, 越具体小鲶越知道你卡在哪)" onChange={(e) => put({ thinking: e.target.value })}
        style={{ ...inputStyle, resize: "vertical" }} />
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        {graded && (
          <button type="button" disabled={disabled} onClick={() => send(true)} style={ghostBtn}>我不知道</button>
        )}
        <button type="button" disabled={disabled || !ready} onClick={() => send()} style={primaryBtn(disabled || !ready)}>提交</button>
      </div>
    </div>
  );
}

export function AnsweredQuestion({ q, answer, grade }: { q: LearnQuestion; answer?: AnswerEv; grade?: GradeEv }) {
  const verdict = grade?.verdict ?? answer?.verdict;
  const correct = new Set(Array.isArray(q.answer) ? q.answer : []);
  return (
    <div style={box}>
      <span style={muted}>{ROLE_LABEL[q.role]}{answer?.skipped ? " · 没答" : ""}</span>
      <Markdown text={hardBreaks(q.question)} />
      {(q.options ?? []).map((o) => {
        const picked = answer?.picked?.includes(o.value);
        const right = correct.has(o.value);
        return (
          <div key={o.value} style={{ fontSize: 13, display: "flex", gap: 8,
            color: right ? "var(--status-ok)" : picked && q.type === "choice" ? "var(--status-err)" : "var(--catfish-text)" }}>
            <span style={{ width: 14 }}>{picked ? "●" : "○"}</span>
            <span><b>{o.value}.</b> {o.label}{right && q.type === "choice" ? " ← 正确答案" : ""}</span>
          </div>
        );
      })}
      {answer?.text && <div style={{ fontSize: 13 }}>你答: {answer.text}</div>}
      {answer && !answer.skipped && (
        <div style={{ ...muted, display: "flex", gap: 12, flexWrap: "wrap" }}>
          {verdict && <span style={{ color: VERDICT[verdict]?.color, fontWeight: 600 }}>{VERDICT[verdict]?.text}</span>}
          {answer.confidence !== undefined && !answer.dont_know && <span>把握 {answer.confidence}%</span>}
          {answer.thinking && <span>想法: {answer.thinking}</span>}
        </div>
      )}
      {grade?.feedback && <div style={{ fontSize: 13 }}>小鲶: {grade.feedback}</div>}
      {q.type === "recall" && typeof q.answer === "string" && (
        <details style={muted}><summary>标准答案</summary><div style={{ color: "var(--catfish-text)" }}>{q.answer}</div></details>
      )}
      {q.explanation && <details style={muted}><summary>解析</summary><div style={{ color: "var(--catfish-text)" }}>{q.explanation}</div></details>}
    </div>
  );
}

/** 11/24 unslop: 按钮不换行、不被挤压; 高 32 (小号 24), 图标 + 字横排 */
export const ghostBtn: CSSProperties = {
  display: "inline-flex", alignItems: "center", justifyContent: "center", minHeight: 32, padding: "4px 12px",
  fontSize: 13, borderRadius: "var(--radius-sm)", border: "1px solid var(--catfish-border)",
  background: "transparent", color: "var(--catfish-text)", cursor: "pointer", fontFamily: "inherit",
  whiteSpace: "nowrap", flex: "none",
};
/** 小号按钮 (听 / 慢点 / 删这份) */
export const smallBtn: CSSProperties = { ...ghostBtn, fontSize: 12, minHeight: 24, padding: "0 8px" };
/** 一屏只有一个实色主按钮; 其余要强调的用这个青色描边 */
export const accentBtn = (disabled: boolean): CSSProperties => ({
  ...ghostBtn, borderColor: "var(--catfish-cyan)", color: "var(--catfish-cyan)",
  opacity: disabled ? 0.5 : 1, cursor: disabled ? "default" : "pointer",
});
export const primaryBtn = (disabled: boolean): CSSProperties => ({
  ...ghostBtn, border: "1px solid var(--catfish-cyan)", background: "var(--catfish-cyan)", color: "#fff",
  opacity: disabled ? 0.5 : 1, cursor: disabled ? "default" : "pointer",
});
