/**
 * 学习里的练习 (11/18 第 2 步): 题、提示、结论在对话里怎么显示; 做题框 (交答案 / 要提示 / 不做了); 做完一题停下来等「下一题」。
 * 提示一次只升一级 (R1 指方向 → R5 几乎整个解法), 标准解法这题结束后才显示。
 */
import { useState, type CSSProperties } from "react";

import { Markdown } from "../../lib/markdown";
import type { LearnEvent, PracticeMode, PracticeSummary } from "../../lib/tauri_learn";
import { inputStyle } from "../Collab/roomLinkUi";
import { ghostBtn, hardBreaks, primaryBtn } from "./QuestionCard";

type Ev<K extends LearnEvent["kind"]> = Extract<LearnEvent, { kind: K }>;
const muted: CSSProperties = { fontSize: 12, color: "var(--catfish-text-muted)" };
const box: CSSProperties = {
  border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-md)", padding: "12px 16px",
  display: "flex", flexDirection: "column", gap: 8, background: "var(--catfish-bg-elevated)",
};

export const MODE_LABEL: Record<PracticeMode, string> = { recall: "想得起来", discrimination: "分得清", composition: "串得起来" };
const OUTCOME: Record<string, { text: string; color: string }> = {
  solved: { text: "✓ 自己做对了", color: "var(--status-ok)" },
  solvedWithHints: { text: "◐ 靠提示做对", color: "var(--status-warn)" },
  movedOn: { text: "✗ 这题没做对, 先过", color: "var(--status-err)" },
};
const MISS: Record<string, string> = { cue: "想不起该用哪条", discrimination: "跟另一条搞混", execution: "用对了但做错",
  careless: "粗心", structural: "没理解那条" };
const RUNG: Record<string, string> = { R1: "第 1 级 · 指方向", R2: "第 2 级 · 用哪类规定", R3: "第 3 级 · 第一步",
  R4: "第 4 级 · 关键一步", R5: "第 5 级 · 几乎整个解法" };

export function ProblemView({ ev, conclude, titles }: { ev: Ev<"problem">; conclude?: Ev<"conclude">; titles: Map<string, string> }) {
  return (
    <div style={{ ...box, borderColor: conclude ? "var(--catfish-border)" : "var(--catfish-cyan)" }}>
      <span style={muted}>练习 · {MODE_LABEL[ev.p.mode] ?? ev.p.mode}{conclude ? ` · 考的是: ${ev.p.targets.map((t) => titles.get(t) ?? t).join("、")}` : ""}</span>
      <Markdown text={hardBreaks(ev.p.statement)} />
      {conclude && (
        <>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", fontSize: 13 }}>
            <b style={{ color: OUTCOME[conclude.outcome]?.color }}>{OUTCOME[conclude.outcome]?.text}</b>
            {conclude.rung && <span style={muted}>用到提示 {RUNG[conclude.rung] ?? conclude.rung}</span>}
            {conclude.miss && <span style={muted}>原因: {MISS[conclude.miss] ?? conclude.miss}</span>}
            {conclude.retest && <span style={muted}>过几题换个情境再考</span>}
          </div>
          {conclude.verdict && <div style={{ fontSize: 13 }}>{conclude.verdict}</div>}
          {ev.p.solution && (
            <details style={muted}><summary>标准解法</summary><div style={{ color: "var(--catfish-text)", whiteSpace: "pre-wrap" }}>{ev.p.solution}</div></details>
          )}
        </>
      )}
    </div>
  );
}

export function PracticeEvent({ ev }: { ev: LearnEvent }) {
  switch (ev.kind) {
    case "attempt":
      return (
        <div style={{ alignSelf: "flex-end", maxWidth: "75%", background: "var(--catfish-bg-cream)", borderRadius: "var(--radius-md)",
          padding: "8px 12px", fontSize: 13, whiteSpace: "pre-wrap" }}>
          {ev.text}{ev.thinking ? <div style={muted}>想法: {ev.thinking}</div> : null}
        </div>
      );
    case "hint_request":
      return <div style={{ ...muted, alignSelf: "flex-end" }}>要了一个提示</div>;
    case "hint":
      return (
        <div style={{ fontSize: 13, borderLeft: "3px solid var(--status-warn)", padding: "4px 12px" }}>
          <span style={muted}>提示 ({RUNG[ev.rung] ?? ev.rung})</span>
          <Markdown text={ev.text} />
        </div>
      );
    case "give_up":
      return <div style={{ ...muted, alignSelf: "flex-end" }}>这题不做了</div>;
    case "cards":
      return <div style={muted}>做了 {ev.added} 张复习卡{ev.skipped ? ` (${ev.skipped} 张跟已有的重复, 没加)` : ""}, 明天开始复习。</div>;
    case "graph":
      return <div style={muted}>沉淀了 {ev.added} 条新要点{ev.linked ? `, ${ev.linked} 条跟以前学过的是同一条 (合并了)` : ""}{ev.chains ? `, ${ev.chains} 条推理` : ""}。</div>;
    default:
      return null;
  }
}

const drafts = new Map<string, { text: string; thinking: string }>();
export const _clearPracticeDrafts = () => drafts.clear();

export function AttemptBox({ pid, disabled, hintsUsed, onAttempt, onHint, onGiveUp }: {
  pid: string; disabled: boolean; hintsUsed: number;
  onAttempt: (text: string, thinking: string) => void; onHint: () => void; onGiveUp: () => void;
}) {
  const [d, setD] = useState(() => drafts.get(pid) ?? { text: "", thinking: "" });
  const put = (p: Partial<typeof d>) => setD((cur) => { const n = { ...cur, ...p }; drafts.set(pid, n); return n; });
  const send = () => { drafts.delete(pid); onAttempt(d.text.trim(), d.thinking.trim()); setD({ text: "", thinking: "" }); };
  return (
    <div style={{ ...box, borderColor: "var(--catfish-cyan)" }} aria-label="做题">
      <textarea aria-label="你的答案" rows={3} value={d.text} disabled={disabled} onChange={(e) => put({ text: e.target.value })}
        placeholder="你的答案 (写清怎么判断的)" style={{ ...inputStyle, resize: "vertical" }} />
      <textarea aria-label="练习想法" rows={2} value={d.thinking} disabled={disabled} onChange={(e) => put({ thinking: e.target.value })}
        placeholder="卡在哪 / 怎么想的 (可选; 要提示时小鲶靠这个判断你卡在哪)" style={{ ...inputStyle, resize: "vertical" }} />
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", flexWrap: "wrap" }}>
        <button type="button" style={ghostBtn} disabled={disabled} onClick={onGiveUp}>不做了, 看解法</button>
        <button type="button" style={ghostBtn} disabled={disabled || hintsUsed >= 5} onClick={onHint}>
          要提示{hintsUsed ? ` (已用 ${hintsUsed} 级)` : ""}
        </button>
        <button type="button" style={primaryBtn(disabled || !d.text.trim())} disabled={disabled || !d.text.trim()} onClick={send}>交答案</button>
      </div>
    </div>
  );
}

export function PracticeSummaryLine({ p }: { p?: PracticeSummary }) {
  if (!p?.total) return null;
  const miss = Object.entries(p.misses).map(([k, n]) => `${k} ${n}`).join("、");
  return (
    <span style={muted}>
      练了 {p.total} 题: 自己做对 {p.solved} · 靠提示 {p.solvedWithHints} · 没做对 {p.movedOn}{miss ? ` (原因: ${miss})` : ""}
    </span>
  );
}
