/**
 * 一个学习会话 (11/18, docs/LEARNING-PLAN.md): 上面路线图, 中间对话 (讲解 / 题 / 评估), 最下面当前的题和插话框。
 * 模型在 tool-bridge 后台跑, 这里在 running 时每 1.5 秒拉一次; 切走再回来从后端重新读, 不丢。
 */
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";

import { Books, Cards, Microphone, PencilSimpleLine } from "@phosphor-icons/react";

import { AskSources } from "../../components/AskSources";
import { Ico } from "../../components/icons";
import { Markdown } from "../../lib/markdown";
import {
  learnAction, learnAnswer, learnAttempt, learnDelete, learnDrill, learnGet, learnLang, learnPractice, learnRetry, learnSay, LANG_NAMES, PHASE_LABEL,
  type LearnAnswer, type LearnEvent, type LearnView,
} from "../../lib/tauri_learn";
import { cardStyle, ErrorLine, inputStyle } from "../Collab/roomLinkUi";
import PlanMap from "./PlanMap";
import { DrillAnswerBox, DrillEval, DrillSummary, ExaminerQuestion, OralAnswer, useVoiceOn } from "./DrillParts";
import { LangFix, LangLine, LangRecap, LangSay, LangScene, LangStart, ShadowSet } from "./LangParts";
import { AttemptBox, PracticeEvent, PracticeSummaryLine, ProblemView } from "./PracticeParts";
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
  const [voiceOn, setVoiceOn] = useVoiceOn();
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
  // 删除点两下: Tauri WebView 里 window.confirm() 静默返回 null (CronJobsCard 7/6 记过), 跟会议删除同一个做法
  const [confirmDel, setConfirmDel] = useState(false);
  useEffect(() => {
    if (!confirmDel) return;
    const t = window.setTimeout(() => setConfirmDel(false), 3000);
    return () => window.clearTimeout(t);
  }, [confirmDel]);
  const remove = async () => {
    if (!confirmDel) { setConfirmDel(true); return; }
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
  const taught = !!s.plan && s.phase !== "probe" && evs.some((e) => e.kind === "say" && e.role === "tutor");
  const concludes = new Map(evs.filter((e): e is Ev<"conclude"> => e.kind === "conclude").map((e) => [e.pid, e]));
  const titles = new Map((s.plan?.nodes ?? []).map((n) => [n.id, n.title]));
  const activePid = s.problem?.pid ?? null;
  const hintsUsed = activePid ? evs.filter((e) => e.kind === "hint" && e.pid === activePid).length : 0;
  const drillEvs = evs.filter((e) => e.kind === "examiner_q" || e.kind === "oral_answer" || e.kind === "drill_eval" || e.kind === "drill_summary");
  const drillLast = drillEvs[drillEvs.length - 1];
  const lastAskId = [...evs].reverse().find((e) => e.kind === "examiner_q")?.id;
  const code = s.lang?.code ?? "en";
  const lastShadowSet = [...evs].reverse().find((e) => e.kind === "shadow_set")?.id;
  const shadowResults = new Map<number, Ev<"shadow_result">>();
  {
    const at = evs.findIndex((e) => e.id === lastShadowSet);
    for (const e of evs.slice(Math.max(0, at))) if (e.kind === "shadow_result") shadowResults.set(e.idx, e);
  }
  const lastLine = [...evs].reverse().find((e) => e.kind === "lang_line" || e.kind === "lang_scene" || e.kind === "lang_recap");
  const lastRoleLineId = [...evs].reverse().find((e) => e.kind === "lang_line" && e.who === "role")?.id;
  // 一屏一个实色主按钮: 有题要交 / 等「开始学」/ 练习的「交答案」「下一题」时, 底下的「发送」退成描边
  const otherPrimary = !!s.pending || (s.phase === "plan_review" && !busy)
    || (s.phase === "practice" && (!!activePid || (!busy && !s.last_error && !s.stalled)));

  return (
    <div style={{ ...cardStyle, height: "auto", gap: "var(--space-3)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 600 }}>{s.goal}</div>
          <div style={muted}>{PHASE_LABEL[s.phase]} · {s.mode === "deep" ? "深入" : s.mode === "defense" ? "答辩陪练" : s.mode === "language" ? (LANG_NAMES[code] ?? code) : "快速"}
            {s.plan ? ` · 路线图 ${s.visited?.length ?? 0}/${s.plan.nodes.length}` : ""}</div>
          <PracticeSummaryLine p={s.practice} />
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
          {s.plan && s.phase !== "practice" && s.phase !== "probe" && s.phase !== "plan_review" && (
            <button type="button" style={ghostBtn} disabled={busy} onClick={() => void act(() => learnPractice(id))}><Ico as={PencilSimpleLine} />去练习</button>
          )}
          {s.phase === "practice" || s.phase === "drill" ? (
            <button type="button" style={ghostBtn} disabled={busy} onClick={() => void act(() => learnAction(id, "teach"))}>回去接着学</button>
          ) : s.phase !== "probe" && s.phase !== "plan_review" && s.phase !== "lang" ? (
            <button type="button" style={ghostBtn} disabled={busy} title="小鲶扮评审老师, 按资料出一套现场会问的题, 你口头回答, 逐条对照评分点"
              onClick={() => void act(() => learnDrill(id, "start"))}><Ico as={Microphone} />答辩陪练</button>
          ) : null}
          {s.phase === "drill" && (s.drill?.done ?? false) && (
            <button type="button" style={ghostBtn} disabled={busy} onClick={() => void act(() => learnDrill(id, "start"))}><Ico as={Microphone} />再来一轮</button>
          )}
          {taught && (
            <button type="button" style={ghostBtn} disabled={busy} title="把讲过的要点做成复习卡, 明天开始按遗忘规律复习"
              onClick={() => void act(() => learnAction(id, "cards"))}><Ico as={Cards} />做成复习卡{s.cards ? ` (已有 ${s.cards})` : ""}</button>
          )}
          {taught && (
            <button type="button" style={ghostBtn} disabled={busy} title="把讲过的提炼成要点和推理, 存进「我学过的要点」"
              onClick={() => void act(() => learnAction(id, "ingest"))}><Ico as={Books} />沉淀要点</button>
          )}
          <button type="button" style={{ ...ghostBtn, ...(confirmDel ? { color: "var(--status-err)", borderColor: "var(--status-err)" } : null) }}
            title="讲解、做过的题都会删掉 (已经做成的复习卡留着)" onClick={() => void remove()} disabled={running}>
            {confirmDel ? "再点一次确认删除" : "删除"}
          </button>
        </div>
      </div>
      {s.plan && (
        <details open style={{ fontSize: 13 }}>
          <summary style={{ cursor: "pointer", ...muted }}>学习路线 (鼠标停在知识点上看要先懂什么)</summary>
          <div style={{ marginTop: 8 }}><PlanMap plan={s.plan} current={s.current} visited={s.visited} /></div>
        </details>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {evs.map((ev) => {
          switch (ev.kind) {
            case "say":
              return ev.role === "learner" ? (
                <div key={ev.id} style={{ alignSelf: "flex-end", maxWidth: "75%", background: "var(--catfish-bg-cream)",
                  borderRadius: "var(--radius-md)", padding: "8px 12px", fontSize: 13, whiteSpace: "pre-wrap" }}>{ev.text}</div>
              ) : (
                <div key={ev.id} style={{ fontSize: 14, lineHeight: 1.7 }}>
                  <Markdown text={ev.text} />
                  <AskSources sources={ev.sources} style={muted} />
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
                  <ul style={{ margin: "8px 0", paddingLeft: 20 }}>
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
                  <div style={{ fontWeight: 600 }}>{ev.revised ? "小鲶改了路线图" : "学习路线"}</div>
                  {ev.approach && <Markdown text={ev.approach} />}
                  {ev.id === lastPlan && s.phase === "plan_review" && !busy && (
                    <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}>
                      <button type="button" style={primaryBtn(false)} onClick={() => sendSay(true)}>就这样, 开始学</button>
                      <span style={muted}>要改就在下面说 (例: 「分级我会了, 不用讲」)</span>
                    </div>
                  )}
                </div>
              );
            case "finish":
              return (
                <div key={ev.id} style={{ border: "1px solid var(--catfish-cyan)", borderRadius: "var(--radius-md)", padding: "12px 16px" }}>
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>这次学完了</div>
                  <Markdown text={ev.summary} />
                  <div style={muted}>还想接着学, 在下面说就行。</div>
                </div>
              );
            case "problem":
              return <ProblemView key={ev.id} ev={ev} conclude={concludes.get(ev.pid)} titles={titles} />;
            case "attempt": case "hint_request": case "hint": case "give_up": case "cards": case "graph":
              return <PracticeEvent key={ev.id} ev={ev} />;
            case "examiner_q":
              return <ExaminerQuestion key={ev.id} ev={ev} autoSpeak={voiceOn && s.phase === "drill" && ev.id === lastAskId && drillLast?.id === ev.id} />;
            case "oral_answer":
              return <OralAnswer key={ev.id} ev={ev} />;
            case "drill_eval":
              return <DrillEval key={ev.id} ev={ev} />;
            case "drill_summary":
              return <DrillSummary key={ev.id} ev={ev} />;
            case "lang_scene":
              return <LangScene key={ev.id} ev={ev} />;
            case "lang_line":
              return <LangLine key={ev.id} ev={ev} code={code} autoSpeak={voiceOn && ev.id === lastRoleLineId && lastLine?.id === ev.id} />;
            case "lang_fix":
              return <LangFix key={ev.id} ev={ev} code={code} />;
            case "shadow_set":
              return ev.id === lastShadowSet && s.lang?.activity === "shadow" ? (
                <ShadowSet key={ev.id} ev={ev} code={code} results={shadowResults} disabled={busy}
                  onRead={(idx, heard, sec) => void act(() => learnLang(id, "shadow", { idx, heard, secs: sec }))}
                  onEnd={() => void act(() => learnLang(id, "end"))} />
              ) : <div key={ev.id} style={muted}>跟读了 {ev.sentences.length} 句</div>;
            case "lang_recap":
              return <LangRecap key={ev.id} ev={ev} code={code} />;
            default:
              return null;
          }
        })}
        {s.phase === "lang" && !s.lang?.activity && !busy && (
          <LangStart code={code} disabled={busy} onStart={(activity, scene) => void act(() => learnLang(id, "start", { activity, scene }))} />
        )}
        {s.phase === "lang" && s.lang?.activity === "dialogue" && !s.lang.ending && lastLine?.kind === "lang_line" && lastLine.who === "role" && (
          <LangSay code={code} disabled={busy} onSay={(t, sec) => void act(() => learnLang(id, "say", { text: t, secs: sec }))}
            onEnd={() => void act(() => learnLang(id, "end"))} />
        )}
        {s.phase === "drill" && drillLast?.kind === "examiner_q" && (
          <DrillAnswerBox disabled={busy} onAnswer={(t, sec) => void act(() => learnDrill(id, "answer", t, sec))}
            onSkip={() => void act(() => learnDrill(id, "skip"))} />
        )}
        {s.phase === "drill" && drillLast?.kind === "drill_eval" && !busy && !s.last_error && (
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button type="button" style={primaryBtn(false)} onClick={() => void act(() => learnDrill(id, "next"))}>
              {s.drill && s.drill.idx + 1 >= s.drill.total ? "看总结" : "下一题"}
            </button>
            <span style={muted}>看完点评再继续</span>
          </div>
        )}
        {s.phase === "practice" && activePid && (
          <AttemptBox key={activePid} pid={activePid} disabled={busy} hintsUsed={hintsUsed}
            onAttempt={(t, th) => void act(() => learnAttempt(id, t, th || undefined))}
            onHint={() => void act(() => learnAction(id, "hint"))}
            onGiveUp={() => void act(() => learnAction(id, "give_up"))} />
        )}
        {s.phase === "practice" && !activePid && !busy && !s.last_error && !s.stalled && (
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button type="button" style={primaryBtn(false)} onClick={() => void act(() => learnPractice(id, true))}>下一题</button>
            <span style={muted}>做完一题先停一下, 想清楚了再来下一题</span>
          </div>
        )}
        {(() => {
          const pend = s.pending && evs.find((e): e is Ev<"question"> => e.kind === "question" && e.qid === s.pending?.qid);
          return pend ? (
            <OpenQuestion key={pend.qid} qid={pend.qid} q={pend.q} disabled={busy}
              onSubmit={(a: LearnAnswer) => void act(() => learnAnswer(id, a))} />
          ) : null;
        })()}
        {running && <div style={muted}>小鲶在想 (查资料、出题、讲解, 一般十几秒到一分钟; 可以先切去干别的, 回来就在)…</div>}
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

      {s.phase === "drill" || s.phase === "lang" ? (
        <label style={{ ...muted, display: "flex", gap: 8, alignItems: "center" }}>
          <input type="checkbox" checked={voiceOn} onChange={(e) => setVoiceOn(e.target.checked)} />
          {s.phase === "drill" ? "评审提问" : "小鲶的台词"}自动读出来 (用电脑自带的语音, 不联网)
        </label>
      ) : (
      <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
        <textarea aria-label="跟小鲶说" rows={2} value={say} disabled={busy} onChange={(e) => putSay(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); sendSay(); } }}
          placeholder={s.pending ? "插话 / 提问 (这道题会先跳过)" : s.phase === "plan_review" ? "想怎么改路线图" : "跟小鲶说"}
          style={{ ...inputStyle, resize: "vertical", flex: 1 }} />
        <button type="button" style={otherPrimary ? { ...ghostBtn, opacity: busy || !say.trim() ? 0.5 : 1 } : primaryBtn(busy || !say.trim())} disabled={busy || !say.trim()} onClick={() => sendSay()}>发送</button>
      </div>
      )}
    </div>
  );
}
