/**
 * 仪表盘「技能 → 验证题」(11/27)。
 *
 * 小鲶改技能时, 拿员工定的口径出的题考一遍新内容: 原来答对的题改完答错了, 这次改动就撤回 (hermes 插件 skill_eval_gate.py)。
 * 题是照员工在对话里的原话自动出的 (「记住…」「以后都…」「不对, 应该…」), 先放「待确认」——员工点「确认」才拿来拦改动。
 * 老员工点「从历史对话出题」, 把以前定过的口径一次补出来 (也都是待确认)。
 * 下面列最近被拦下 / 撤回的改动, 让员工知道门槛在干活, 也能看出是不是哪道题定错了。
 */
import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import { Btn, CardHead, ErrorLine, cardStyle, chipStyle, inputStyle, itemStyle } from "../Collab/roomLinkUi";

interface EvalQuestion {
  skill: string;
  id: string;
  q: string;
  gold: string;
  status: string;
  origin: string;
  source: string;
  at: string;
  changed: boolean;
}

interface GateLog {
  ts?: string;
  skill?: string;
  reverted?: boolean;
  blocked?: boolean;
  worse?: string[];
  conflicts?: string[];
  why?: string;
}

interface EvalSummary {
  pending: EvalQuestion[];
  confirmed: EvalQuestion[];
  skills: number;
  log: GateLog[];
}

const muted = { fontSize: 12, color: "var(--catfish-text-muted)", lineHeight: 1.5 } as const;

function QuestionRow({ item, busy, onAction }: {
  item: EvalQuestion;
  busy: boolean;
  onAction: (item: EvalQuestion, action: "confirm" | "delete" | "edit", gold?: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [gold, setGold] = useState(item.gold);
  return (
    <div style={itemStyle}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: "var(--space-3)", alignItems: "flex-start" }}>
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
          <div style={{ fontSize: 13, fontWeight: 500 }}>{item.q}</div>
          {editing ? (
            <textarea value={gold} onChange={(e) => setGold(e.target.value)} rows={2} style={{ ...inputStyle, fontSize: 12 }} />
          ) : (
            <div style={{ fontSize: 12 }}>答案: {item.gold}</div>
          )}
          {(item.source || item.changed) && (
            <div style={muted}>
              {item.source}
              {item.changed ? `${item.source ? " · " : ""}口径改过` : ""}
            </div>
          )}
        </div>
        <div style={{ display: "flex", gap: "var(--space-2)", flex: "none" }}>
          {editing ? (
            <>
              <Btn kind="accent" disabled={busy || !gold.trim()} onClick={() => { onAction(item, "edit", gold); setEditing(false); }}>保存</Btn>
              <Btn kind="ghost" disabled={busy} onClick={() => { setGold(item.gold); setEditing(false); }}>取消</Btn>
            </>
          ) : (
            <>
              {item.status === "pending" && <Btn kind="accent" disabled={busy} onClick={() => onAction(item, "confirm")}>确认</Btn>}
              <Btn kind="ghost" disabled={busy} onClick={() => setEditing(true)}>改答案</Btn>
              <Btn kind="ghost" disabled={busy} onClick={() => onAction(item, "delete")}>删除</Btn>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/** 按技能分组, 组内顺序不变 */
function groups(list: EvalQuestion[]): [string, EvalQuestion[]][] {
  const m = new Map<string, EvalQuestion[]>();
  for (const it of list) m.set(it.skill, [...(m.get(it.skill) ?? []), it]);
  return [...m.entries()];
}

function logLine(l: GateLog): string {
  const when = (l.ts ?? "").replace("T", " ").slice(5, 16);
  if (l.blocked) return `${when} 拦下删除「${l.skill}」: ${(l.why ?? "").slice(0, 60)}`;
  const parts = [];
  if (l.worse?.length) parts.push(`${l.worse.length} 道题改完答错`);
  if (l.conflicts?.length) parts.push(`跟 ${l.conflicts.length} 条口径矛盾`);
  return `${when} 撤回「${l.skill}」的改动: ${parts.join("，") || "没通过验证"}`;
}

export default function SkillEvalsCard() {
  const [data, setData] = useState<EvalSummary | null>(null);
  const [view, setView] = useState<"pending" | "confirmed">("pending");
  const [busy, setBusy] = useState(false);
  const [mining, setMining] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setData((await invoke("skill_evals_summary")) as EvalSummary);
      setError(null);
    } catch (e) {
      setError(`读验证题失败: ${e}`);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function onAction(item: EvalQuestion, action: "confirm" | "delete" | "edit", gold?: string) {
    setBusy(true);
    try {
      await invoke("skill_evals_update", { skill: item.skill, id: item.id, action, gold: gold ?? null });
      await refresh();
    } catch (e) {
      setError(`${action === "confirm" ? "确认" : action === "delete" ? "删除" : "保存"}失败: ${e}`);
    } finally {
      setBusy(false);
    }
  }

  async function confirmAll(skill: string) {
    setBusy(true);
    try {
      await invoke("skill_evals_update", { skill, id: "", action: "confirm_all", gold: null });
      await refresh();
    } catch (e) {
      setError(`确认失败: ${e}`);
    } finally {
      setBusy(false);
    }
  }

  async function mine() {
    setMining(true);
    setMsg(null);
    try {
      const got = (await invoke("skill_evals_mine")) as Record<string, number>;
      const n = Object.values(got).reduce((a, b) => a + b, 0);
      setMsg(n ? `从历史对话出了 ${n} 道题, 在「待确认」里` : "历史对话里没找到新的口径");
      setView("pending");
      await refresh();
    } catch (e) {
      setError(`${e}`);
    } finally {
      setMining(false);
    }
  }

  const list = data ? (view === "pending" ? data.pending : data.confirmed) : [];
  return (
    <div style={cardStyle}>
      <CardHead
        title="验证题"
        sub="小鲶改技能时, 用你定的口径考一遍改完的内容, 原来答对的题答错了就撤回这次改动。题照你的原话出, 确认后才生效。"
        aside={<Btn kind="ghost" disabled={mining} onClick={() => void mine()}>{mining ? "正在出题…" : "从历史对话出题"}</Btn>}
      />
      {error && <ErrorLine>{error}</ErrorLine>}
      {msg && <div style={muted}>{msg}</div>}
      {!data ? (
        <div style={muted}>加载中…</div>
      ) : (
        <>
          <div style={{ display: "flex", gap: "var(--space-2)" }}>
            <button type="button" style={chipStyle(view === "pending")} onClick={() => setView("pending")}>
              待确认 {data.pending.length}
            </button>
            <button type="button" style={chipStyle(view === "confirmed")} onClick={() => setView("confirmed")}>
              已生效 {data.confirmed.length}
            </button>
          </div>
          {list.length === 0 ? (
            <div style={muted}>
              {view === "pending"
                ? "没有待确认的题。你在对话里定口径时 (「记住…」「以后都…」), 小鲶会照原话出题放到这里。"
                : "还没有生效的题。"}
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)", overflowY: "auto", maxHeight: 480 }}>
              {groups(list).map(([skill, items]) => (
                <div key={skill} style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "var(--space-2)" }}>
                    <div style={{ fontSize: 12, fontWeight: 500 }}>{skill} · {items.length} 道</div>
                    {view === "pending" && items.length > 1 && (
                      <Btn kind="ghost" disabled={busy} onClick={() => void confirmAll(skill)}>这组全部确认</Btn>
                    )}
                  </div>
                  {items.map((it) => <QuestionRow key={`${it.skill}/${it.id}`} item={it} busy={busy} onAction={onAction} />)}
                </div>
              ))}
            </div>
          )}
          {data.log.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
              <div style={{ fontSize: 12, fontWeight: 500 }}>最近拦下的改动</div>
              {data.log.slice(0, 6).map((l, i) => <div key={i} style={muted}>{logLine(l)}</div>)}
            </div>
          )}
        </>
      )}
    </div>
  );
}
