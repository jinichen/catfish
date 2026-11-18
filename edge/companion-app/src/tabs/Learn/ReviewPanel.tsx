/**
 * 复习 (11/18, docs/LEARNING-PLAN.md §5): 今天到期的卡一张一张来 —— 先自己想 (可以写下来), 再看答案, 按想起来的程度点四档之一,
 * 下次复习排到哪天写在按钮上 (FSRS, tool-bridge fsrs.py)。题库里导入的选择题 / 判断题直接选, 这里判对错。
 * 卡来自: 学习学完自动做的、会议纪要「记住这几条」、小鲶对话里出过答错的题。
 */
import { useCallback, useEffect, useState, type CSSProperties } from "react";

import {
  learnReviewAnswer, learnReviewNext, RATING_LABEL,
  type Previews, type Rating, type ReviewCard, type ReviewResult, type ReviewStats,
} from "../../lib/tauri_learn";
import { cardStyle, ErrorLine, inputStyle } from "../Collab/roomLinkUi";
import { ghostBtn, primaryBtn } from "./QuestionCard";

const muted: CSSProperties = { fontSize: 12, color: "var(--catfish-text-muted)" };
const OBJECTIVE = new Set(["single_choice", "multi_choice", "true_false"]);
const RATINGS: Rating[] = ["again", "hard", "good", "easy"];

export default function ReviewPanel({ onChanged }: { onChanged?: () => void }) {
  const [cards, setCards] = useState<ReviewCard[] | null>(null);
  const [stats, setStats] = useState<ReviewStats | null>(null);
  const [previews, setPreviews] = useState<Previews | null>(null);
  const [mine, setMine] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [shown, setShown] = useState<ReviewResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(0);

  const load = useCallback(async () => {
    try {
      const r = await learnReviewNext();
      setCards(r.questions);
      setStats(r.stats);
      setPreviews(r.questions[0] ? r.previews[String(r.questions[0].id)] ?? null : null);
      setMine("");
      setPicked([]);
      setShown(null);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const card = cards?.[0];
  const objective = !!card && OBJECTIVE.has(card.type);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try { await fn(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  const reveal = () => run(async () => {
    if (!card) return;
    if (objective) {
      const r = await learnReviewAnswer(card.id, card.type === "true_false" ? picked[0] : picked.join(","));
      setShown(r);
      setDone((n) => n + 1);
      onChanged?.();
    } else {
      const r = await learnReviewAnswer(card.id, mine.trim() || undefined);
      setShown(r);
      if (r.previews) setPreviews(r.previews);
    }
  });
  const rate = (rating: Rating) => run(async () => {
    if (!card) return;
    await learnReviewAnswer(card.id, mine.trim() || undefined, rating);
    setDone((n) => n + 1);
    onChanged?.();
    await load();
  });

  if (error && !cards) return <div style={cardStyle}><ErrorLine>{error}</ErrorLine></div>;
  if (!cards) return <div style={{ ...cardStyle, ...muted }}>读取中…</div>;
  return (
    <div style={{ ...cardStyle, height: "auto" }} aria-label="复习">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div style={{ fontSize: 16, fontWeight: 600 }}>🔁 今天的复习</div>
        <span style={muted}>还剩 {cards.length} 条{done ? ` · 刚复习了 ${done} 条` : ""}{stats ? ` · 卡片一共 ${stats.total} 张` : ""}</span>
      </div>
      {!card ? (
        <div style={{ fontSize: 14, padding: "24px 0", textAlign: "center" }}>
          🎉 今天的复习做完了。<div style={muted}>学完的内容会自动做成复习卡, 会议纪要上也可以点「记住这几条」。</div>
        </div>
      ) : (
        <>
          {card.tags.length > 0 && <span style={muted}>{card.tags.join(" · ")}</span>}
          <div style={{ fontSize: 15, lineHeight: 1.7, whiteSpace: "pre-wrap" }}>{card.question}</div>
          {objective && (card.type === "true_false" ? [["true", "对"], ["false", "错"]] : Object.entries(card.options)).map(([k, label]) => (
            <label key={k} style={{ display: "flex", gap: 8, fontSize: 13, cursor: "pointer" }}>
              <input type={card.type === "multi_choice" ? "checkbox" : "radio"} name={`r${card.id}`} disabled={!!shown || busy}
                checked={picked.includes(k)}
                onChange={() => setPicked(card.type === "multi_choice" ? (picked.includes(k) ? picked.filter((x) => x !== k) : [...picked, k]) : [k])} />
              <span>{card.type === "true_false" ? label : <><b>{k}.</b> {label}</>}</span>
            </label>
          ))}
          {!objective && !shown && (
            <textarea aria-label="先自己想" rows={3} value={mine} onChange={(e) => setMine(e.target.value)} disabled={busy}
              placeholder="先自己想一想 (可以写下来, 也可以直接看答案)" style={{ ...inputStyle, resize: "vertical" }} />
          )}
          {!shown ? (
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <button type="button" disabled={busy || (objective && picked.length === 0)} onClick={() => void reveal()}
                style={primaryBtn(busy || (objective && picked.length === 0))}>{objective ? "提交" : "看答案"}</button>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {objective && (
                <div style={{ fontWeight: 600, color: shown.correct ? "var(--status-ok)" : "var(--status-err)" }}>
                  {shown.correct ? "✓ 对" : `✗ 不对, 答案是 ${shown.answer}`}
                </div>
              )}
              {!objective && (
                <div style={{ borderLeft: "3px solid var(--catfish-cyan)", padding: "6px 10px", background: "var(--catfish-cyan-tint)", fontSize: 14 }}>
                  {shown.answer}
                </div>
              )}
              {!objective && mine.trim() && <div style={muted}>你写的: {mine}</div>}
              {shown.explanation && shown.explanation !== shown.answer && <div style={muted}>要点: {shown.explanation}</div>}
              {shown.source && <div style={muted}>来自: {shown.source}</div>}
              {objective ? (
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={muted}>{shown.next_review ? `下次: ${shown.next_review}` : ""}</span>
                  <button type="button" style={primaryBtn(busy)} disabled={busy} onClick={() => void load()}>下一条</button>
                </div>
              ) : (
                <>
                  <span style={muted}>对照一下, 你想起来了多少?</span>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 8 }}>
                    {RATINGS.map((r) => (
                      <button key={r} type="button" disabled={busy} onClick={() => void rate(r)}
                        style={{ ...ghostBtn, display: "flex", flexDirection: "column", alignItems: "center", gap: 2,
                          borderColor: r === "good" ? "var(--catfish-cyan)" : "var(--catfish-border)" }}>
                        <span>{RATING_LABEL[r]}</span>
                        <span style={muted}>{previews?.[r] ?? ""}</span>
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}
        </>
      )}
      {error && <ErrorLine>{error}</ErrorLine>}
    </div>
  );
}
