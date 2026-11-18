/**
 * 「学习」tab (11/18, docs/LEARNING-PLAN.md) —— 小鲶当教练: 摸底 → 路线图 → 一步一步讲 + 检查。
 * 左栏: 新建学习 + 学过的列表; 右栏: 选中那个学习。记录都在本机 ~/.catfish/learning/, 讲解时查的资料片段经网关给大模型。
 */
import { useCallback, useEffect, useState, type CSSProperties } from "react";

import { learnCreate, learnList, learnReviewNext, PHASE_LABEL, type LearnMode, type LearnSummary } from "../../lib/tauri_learn";
import { useUIStore } from "../../store/ui";
import { cardStyle, ErrorLine, inputStyle } from "../Collab/roomLinkUi";
import { COL_CARD } from "../Meeting/meetingHelpers";
import LearnSession from "./LearnSession";
import ReviewPanel from "./ReviewPanel";
import { primaryBtn } from "./QuestionCard";

const muted: CSSProperties = { fontSize: 12, color: "var(--catfish-text-muted)" };
const REVIEW = "__review__";
const EXAMPLES = ["准备 CCRC 应急处理二级的答辩", "弄懂 ITSS 二级要提交哪些材料、为什么", "公司的差旅报销规定"];

function NewLearning({ onCreated }: { onCreated: (id: string) => void }) {
  const [goal, setGoal] = useState("");
  const [mode, setMode] = useState<LearnMode>("quick");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const v = await learnCreate(goal.trim(), mode);
      setGoal("");
      onCreated(v.session.id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const modeBtn = (m: LearnMode, label: string, hint: string) => (
    <button type="button" onClick={() => setMode(m)} title={hint}
      style={{ flex: 1, padding: "6px 8px", fontSize: 12, borderRadius: "var(--radius-sm)", cursor: "pointer", fontFamily: "inherit",
        border: `1px solid ${mode === m ? "var(--catfish-cyan)" : "var(--catfish-border)"}`,
        background: mode === m ? "var(--catfish-bg-cream)" : "transparent", color: "var(--catfish-text)" }}>{label}</button>
  );
  return (
    <div style={{ ...cardStyle, ...COL_CARD }}>
      <div style={{ fontWeight: 600 }}>🎓 学点什么</div>
      <textarea aria-label="想学会什么" rows={3} value={goal} onChange={(e) => setGoal(e.target.value)} disabled={busy}
        placeholder="想学会什么? 越具体越好, 例: 准备 CCRC 应急处理二级的答辩" style={{ ...inputStyle, resize: "vertical" }} />
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {EXAMPLES.map((x) => (
          <button key={x} type="button" onClick={() => setGoal(x)} disabled={busy}
            style={{ fontSize: 11, padding: "2px 8px", borderRadius: 999, border: "1px solid var(--catfish-border)",
              background: "transparent", color: "var(--catfish-text-muted)", cursor: "pointer", fontFamily: "inherit" }}>{x}</button>
        ))}
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        {modeBtn("quick", "快速", "讲得短、多检查, 适合记规定、准备答辩")}
        {modeBtn("deep", "深入", "每步先让你猜再讲, 讲清为什么, 适合真正弄懂")}
      </div>
      <button type="button" style={primaryBtn(busy || !goal.trim())} disabled={busy || !goal.trim()} onClick={() => void start()}>
        开始 (小鲶先问几道题摸摸底)
      </button>
      <span style={muted}>小鲶会查你的知识库、本机文件、邮件, 需要时上网查官方文件; 学习记录只存在这台电脑上。</span>
      {error && <ErrorLine>{error}</ErrorLine>}
    </div>
  );
}

export default function LearnTab() {
  const [list, setList] = useState<LearnSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [due, setDue] = useState<{ due: number; total: number } | null>(null);
  const pendingReview = useUIStore((s) => s.pendingLearnReview);
  const refreshDue = useCallback(() => {
    learnReviewNext(1).then((r) => setDue({ due: r.stats.due_today, total: r.stats.total })).catch(() => undefined);
  }, []);
  useEffect(() => {
    if (!pendingReview) return;
    useUIStore.setState({ pendingLearnReview: false });
    setSelected(REVIEW);
  }, [pendingReview]);
  const refresh = useCallback(async (selectId?: string | null) => {
    refreshDue();
    try {
      const l = await learnList();
      setList(l);
      setError(null);
      setSelected((cur) => (selectId !== undefined ? selectId : cur === REVIEW || (cur && l.some((x) => x.id === cur)) ? cur : l[0]?.id ?? null));
    } catch (e) {
      setError(String(e));
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  const changed = useCallback((deleted?: boolean) => void refresh(deleted ? null : undefined), [refresh]);

  return (
    <div style={{ maxWidth: 1600, margin: "0 auto", padding: "var(--space-4) var(--space-4) 0", height: "100%", boxSizing: "border-box" }}>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(260px, 340px) minmax(0, 1fr)", gap: "var(--space-4)", height: "100%" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)", minHeight: 0, overflowY: "auto", paddingBottom: "var(--space-4)" }}>
          <NewLearning onCreated={(id) => void refresh(id)} />
          <button type="button" onClick={() => setSelected(REVIEW)} aria-label="去复习"
            style={{ ...cardStyle, ...COL_CARD, cursor: "pointer", textAlign: "left", fontFamily: "inherit", color: "var(--catfish-text)",
              borderColor: selected === REVIEW ? "var(--catfish-cyan)" : "var(--catfish-border)", gap: 4 }}>
            <div style={{ fontWeight: 600, fontSize: 13 }}>🔁 复习</div>
            <div style={muted}>{due === null ? "…" : due.due ? `今天要复习 ${due.due} 条` : due.total ? "今天没有要复习的" : "还没有复习卡 (学完会自动做)"}</div>
          </button>
          <div style={{ ...cardStyle, ...COL_CARD, gap: "var(--space-1)" }}>
            <div style={{ fontWeight: 600, fontSize: 13 }}>我的学习</div>
            {list.length === 0 && <div style={muted}>还没有</div>}
            {list.map((m) => (
              <button key={m.id} type="button" onClick={() => setSelected(m.id)}
                style={{ textAlign: "left", padding: "8px 10px", border: "none", borderRadius: "var(--radius-sm)", cursor: "pointer",
                  background: m.id === selected ? "var(--catfish-bg-cream)" : "transparent", color: "var(--catfish-text)", fontFamily: "inherit" }}>
                <div style={{ fontSize: 13, fontWeight: 500 }}>{m.goal}</div>
                <div style={muted}>{new Date(m.updated * 1000).toLocaleDateString()} · {PHASE_LABEL[m.phase]}
                  {m.nodes ? ` · ${m.visited}/${m.nodes}` : ""}</div>
              </button>
            ))}
            {error && <ErrorLine>{error}</ErrorLine>}
          </div>
        </div>
        <div style={{ minHeight: 0, overflowY: "auto", paddingBottom: "var(--space-4)" }}>
          {selected === REVIEW ? <ReviewPanel onChanged={refreshDue} />
            : selected ? <LearnSession key={selected} id={selected} onChanged={changed} />
            : <div style={{ ...cardStyle, ...muted }}>左边写一句想学会什么, 小鲶先问几道题摸摸底, 再给你排一条学习路线。</div>}
        </div>
      </div>
    </div>
  );
}
