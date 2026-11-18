/**
 * 一个学习会话 (11/18, docs/LEARNING-PLAN.md): 上面路线图, 中间对话 (讲解 / 题 / 评估), 最下面当前的题和插话框。
 * 模型在 tool-bridge 后台跑, 这里在 running 时每 1.5 秒拉一次; 切走再回来从后端重新读, 不丢。
 */
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";

import { AskSources } from "../../components/AskSources";
import { Markdown } from "../../lib/markdown";
import {
  learnAnswer, learnDelete, learnGet, learnRetry, learnSay, PHASE_LABEL,
  type LearnAnswer, type LearnEvent, type LearnView,
} from "../../lib/tauri_learn";
import { cardStyle, ErrorLine, inputStyle } from "../Collab/roomLinkUi";
import PlanMap from "./PlanMap";
import { AnsweredQuestion, ghostBtn, OpenQuestion, primaryBtn } from "./QuestionCard";

const POLL_MS = 1500;
const muted: CSSProperties = { fontSize: 12, color: "var(--catfish-text-muted)" };
const sayDrafts = new Map<string, string>();
export const _clearSayDrafts = () => sayDrafts.clear();

type Ev<K extends LearnEvent["kind"]> = Extract<LearnEvent, { kind: K }>;

export default function LearnSession({ id, onChanged }: { id: string; onChanged: (deleted?: boolean) => void }) {
  const [view, setView] = useState<LearnView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [say, setSay] = useState(() => sayDrafts.get(id) ?? "");
  const [sending, setSending] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      setView(await learnGet(id));
    } catch (e) {
      setError(String(e));
    }
  }, [id]);

  useEffect(() => { setView(null); setError(null); setSay(sayDrafts.get(id) ?? ""); void load(); }, [id, load]);
  const running = !!view?.running;
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(t);
  }, [running, load]);
  // 跑完那一刻通知左边列表刷新阶段
  const wasRunning = useRef(false);
  useEffect(() => { if (wasRunning.current && !running) onChanged(); wasRunning.current = running; }, [running, onChanged]);
  const count = view?.events.length ?? 0;
  useEffect(() => { bottom.current?.scrollIntoView?.({ block: "end" }); }, [count, running]);

  const act = async (fn: () => Promise<LearnView>) => {
    setSending(true);
    setError(null);
    try {
      setView(await fn());
    } catch (e) {
      setError(String(e));
    } finally {
      setSending(false);
    }
  };
  const putSay = (t: string) => { setSay(t); sayDrafts.set(id, t); };
  const sendSay = (start = false) => {
    const t = say.trim();
    if (!t && !start) return;
    putSay("");
    void act(() => learnSay(id, t, start));
  };
  const remove = async () => {
    if (!window.confirm("删掉这个学习? 讲解、做过的题都会删掉, 找不回来。")) return;
    try {
      await learnDelete(id);
      onChanged(true);
    } catch (e) {
      setError(String(e));
    }
  };

  if (!view) return <div style={cardStyle}>{error ? <ErrorLine>{error}</ErrorLine> : <span style={muted}>读取中…</span>}</div>;
  const s = view.session;
  const evs = view.events;
  const answers = new Map(evs.filter((e): e is Ev<"answer"> => e.kind === "answer").map((e) => [e.qid, e]));
  const grades = new Map(evs.filter((e): e is Ev<"grade"> => e.kind === "grade").map((e) => [e.qid, e]));
  const lastPlan = [...evs].reverse().find((e) => e.kind === "plan")?.id;
  const busy = running || sending;

  return (
    <div style={{ ...cardStyle, height: "auto", gap: "var(--space-3)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 600 }}>{s.goal}</div>
          <div style={muted}>{PHASE_LABEL[s.phase]} · {s.mode === "deep" ? "深入" : "快速"}
            {s.plan ? ` · 路线图 ${s.visited?.length ?? 0}/${s.plan.nodes.length}` : ""}</div>
        </div>
        <button type="button" style={ghostBtn} onClick={() => void remove()} disabled={running}>删除</button>
      </div>
      {s.plan && (
        <details open style={{ fontSize: 13 }}>
          <summary style={{ cursor: "pointer", ...muted }}>学习路线 (鼠标停在知识点上看要先懂什么)</summary>
          <div style={{ marginTop: 6 }}><PlanMap plan={s.plan} current={s.current} visited={s.visited} /></div>
        </details>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {evs.map((ev) => {
          switch (ev.kind) {
            case "say":
              return ev.role === "learner" ? (
                <div key={ev.id} style={{ alignSelf: "flex-end", maxWidth: "75%", background: "var(--catfish-bg-cream)",
                  borderRadius: "var(--radius-md)", padding: "8px 12px", fontSize: 13, whiteSpace: "pre-wrap" }}>{ev.text}</div>
              ) : (
                <div key={ev.id} style={{ fontSize: 14, lineHeight: 1.7 }}>
                  <Markdown text={ev.text} />
                  <AskSources sources={ev.sources} style={muted} localIcon="🔎 " />
                </div>
              );
            case "question":
              return ev.qid === s.pending?.qid ? null : (
                <AnsweredQuestion key={ev.id} q={ev.q} answer={answers.get(ev.qid)} grade={grades.get(ev.qid)} />
              );
            case "assessment":
              return (
                <details key={ev.id} style={{ fontSize: 13 }} open={s.phase === "plan_review"}>
                  <summary style={{ cursor: "pointer", fontWeight: 600 }}>摸底结果: {ev.goal}</summary>
                  <ul style={{ margin: "6px 0", paddingLeft: 20 }}>
                    {ev.strands.map((st, i) => (
                      <li key={i}><b>{st.strand}</b>{st.solid ? ` —— 稳拿: ${st.solid}` : ""}{st.gap ? `; 卡在: ${st.gap}` : ""}
                        {st.misconceptions ? `; 误解: ${st.misconceptions}` : ""}</li>
                    ))}
                  </ul>
                  {ev.summary && <div style={muted}>{ev.summary}</div>}
                </details>
              );
            case "plan":
              return (
                <div key={ev.id} style={{ fontSize: 13 }}>
                  <div style={{ fontWeight: 600 }}>{ev.revised ? "🗺 小鲶改了路线图" : "🗺 学习路线"}</div>
                  {ev.approach && <Markdown text={ev.approach} />}
                  {ev.id === lastPlan && s.phase === "plan_review" && !busy && (
                    <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
                      <button type="button" style={primaryBtn(false)} onClick={() => sendSay(true)}>就这样, 开始学</button>
                      <span style={muted}>要改就在下面说 (例: 「分级我会了, 不用讲」)</span>
                    </div>
                  )}
                </div>
              );
            case "finish":
              return (
                <div key={ev.id} style={{ border: "1px solid var(--catfish-cyan)", borderRadius: "var(--radius-md)", padding: "10px 14px" }}>
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>🎓 这次学完了</div>
                  <Markdown text={ev.summary} />
                  <div style={muted}>还想接着学, 在下面说就行。</div>
                </div>
              );
            default:
              return null;
          }
        })}
        {(() => {
          const pend = s.pending && evs.find((e): e is Ev<"question"> => e.kind === "question" && e.qid === s.pending?.qid);
          return pend ? (
            <OpenQuestion key={pend.qid} qid={pend.qid} q={pend.q} disabled={busy}
              onSubmit={(a: LearnAnswer) => void act(() => learnAnswer(id, a))} />
          ) : null;
        })()}
        {running && <div style={muted}>🐟 小鲶在想 (查资料、出题、讲解, 一般十几秒到一分钟; 可以先切去干别的, 回来就在)…</div>}
        {!running && s.last_error && (
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <ErrorLine>{s.last_error}</ErrorLine>
            <button type="button" style={ghostBtn} onClick={() => void act(() => learnRetry(id))}>重试</button>
          </div>
        )}
        {!running && s.stalled && (
          <div style={{ display: "flex", gap: 8, alignItems: "center", ...muted }}>
            上一步没接上 (可能是小鲶的后台重启了), 你说的已经存了。
            <button type="button" style={ghostBtn} onClick={() => void act(() => learnRetry(id))}>重试</button>
          </div>
        )}
        {error && <ErrorLine>{error}</ErrorLine>}
        <div ref={bottom} />
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
        <textarea aria-label="跟小鲶说" rows={2} value={say} disabled={busy} onChange={(e) => putSay(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); sendSay(); } }}
          placeholder={s.pending ? "插话 / 提问 (这道题会先跳过)" : s.phase === "plan_review" ? "想怎么改路线图" : "跟小鲶说"}
          style={{ ...inputStyle, resize: "vertical", flex: 1 }} />
        <button type="button" style={primaryBtn(busy || !say.trim())} disabled={busy || !say.trim()} onClick={() => sendSay()}>发送</button>
      </div>
    </div>
  );
}
