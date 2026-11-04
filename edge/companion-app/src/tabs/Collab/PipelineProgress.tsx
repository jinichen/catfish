/**
 * 多阶段任务的进度 (11/4, docs/COLLEAGUE-TASK-PLAN.md §3)。
 *
 * 每段一行: 还没开始 / 进行中 (人数和状态) / 已放行。点「这一段的详情」打开那一段的任务进度 (加人 / 催 / 请他重做都在那里)。
 * 当前这一段下面是「交给下一段的内容」: 默认是这一段每人交回的结果, 你可以删改; 点「放行到下一段」才发出去。
 * 还有人没交回也可以放行 (不等了), 他们会收到取消通知。最后一段放行 = 完成流程。
 */
import { useCallback, useEffect, useState } from "react";

import { refreshTasks } from "../../lib/colleagueTasksStore";
import { pipelineSummaryPrompt } from "../../lib/taskPrompts";
import { pipelineCancel, pipelineGet, pipelineRelease, type PipelineDetail } from "../../lib/tauri_colleague_tasks";
import { useUIStore } from "../../store/ui";
import { Overlay, StateTag, muted } from "./formsUi";
import { Btn, ErrorLine, inputStyle } from "./roomLinkUi";
import TaskProgress from "./TaskProgress";

const STATUS = { open: "进行中", done: "已完成", cancelled: "已取消" } as const;

export default function PipelineProgress({ pipelineId, onClose, initialError }: {
  pipelineId: string;
  onClose: () => void;
  initialError?: string;
}) {
  const [d, setD] = useState<PipelineDetail | null>(null);
  const [handoff, setHandoff] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(initialError ?? null);
  const [info, setInfo] = useState<string | null>(null);
  const [stageTask, setStageTask] = useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const startChat = useUIStore((s) => s.startEmailChat);

  const load = useCallback(async () => {
    await refreshTasks();
    const r = await pipelineGet(pipelineId);
    setD(r);
    setHandoff(r.handoff);
  }, [pipelineId]);

  useEffect(() => {
    load().catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [load]);

  async function run(fn: () => Promise<string | void>) {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const msg = await fn();
      if (msg) setInfo(msg);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!d) {
    return <Overlay title="多阶段任务" onClose={onClose}>{error ? <ErrorLine>{error}</ErrorLine> : <span style={muted}>读取中…</span>}</Overlay>;
  }
  const p = d.pipeline;
  const cur = p.stages[p.current];
  const last = p.current === p.stages.length - 1;
  const open = p.status === "open";
  const pending = (cur.progress?.rows ?? []).filter((r) => !["已完成", "逾期完成", "不接", "已取消"].includes(r.state)).length;

  if (stageTask) {
    return <TaskProgress taskId={stageTask} onClose={() => { setStageTask(null); void load(); }} />;
  }

  return (
    <Overlay title={`「${p.title}」`} onClose={onClose} width={780}>
      <span style={muted}>{STATUS[p.status]} · 共 {p.stages.length} 段{open ? ` · 现在第 ${p.current + 1} 段` : ""}</span>
      {p.stages.map((st) => {
        const counts = Object.entries(st.progress?.counts ?? {}).map(([k, v]) => `${k} ${v}`).join(" · ");
        const label = st.released_at ? "已放行" : st.task_id ? (st.finished ? "都回了" : "进行中") : "还没开始";
        return (
          <div key={st.index} style={{ display: "flex", flexDirection: "column", gap: 4, padding: 8, borderRadius: 6,
                                       border: `1px solid ${st.index === p.current && open ? "var(--catfish-cyan)" : "var(--catfish-border)"}` }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
              <strong style={{ fontSize: 13 }}>第 {st.index + 1} 段 · {st.name}</strong>
              <StateTag state={label === "都回了" ? "待处理" : label === "进行中" ? "进行中" : label === "已放行" ? "已完成" : "等回应"} />
            </div>
            <span style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>{st.text}</span>
            <span style={muted}>{st.recipients.join("、")}{counts ? ` · ${counts}` : ""}</span>
            {st.task_id && <div><Btn kind="ghost" onClick={() => setStageTask(st.task_id)}>这一段的详情 (结果 / 加人 / 催 / 请他重做)</Btn></div>}
          </div>
        );
      })}

      {open && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {!last && (
            <>
              <strong style={{ fontSize: 13 }}>交给第 {p.current + 2} 段 ({p.stages[p.current + 1].name}) 的内容</strong>
              <span style={muted}>默认是这一段每人交回的结果; 删掉不该给下一个人看的, 再放行。下一段的人只看得到这里的内容。</span>
              <textarea value={handoff} onChange={(e) => setHandoff(e.target.value)} rows={8} style={{ ...inputStyle, resize: "vertical" }}
                        aria-label="交给下一段的内容" />
            </>
          )}
          {pending > 0 && <span style={{ ...muted, color: "var(--status-warn, #d97706)" }}>这一段还有 {pending} 人没交回; 现在放行的话他们会收到取消通知。</span>}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <Btn kind="primary" disabled={busy || (!last && !handoff.trim())} onClick={() => run(async () => {
              const r = await pipelineRelease(pipelineId, handoff);
              if (r.done) return "流程完成了";
              return `已放行到第 ${r.stage} 段, 发给 ${(r.sent ?? []).length} 人${r.failed?.length ? `; ${r.failed.length} 人没发出去` : ""}`;
            })}>{last ? "完成流程" : "放行到下一段"}</Btn>
          </div>
        </div>
      )}

      {error && <ErrorLine>{error}</ErrorLine>}
      {info && <div style={{ fontSize: 12, color: "var(--status-ok, #16a34a)" }}>{info}</div>}

      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <Btn kind="ghost" disabled={busy} onClick={() => { startChat(`ctask-pipeline:${pipelineId}`, pipelineSummaryPrompt(p.title, p.stages)); onClose(); }}>
          🐟 让小鲶汇总各段
        </Btn>
        {open && !confirmCancel && <Btn kind="ghost" disabled={busy} onClick={() => setConfirmCancel(true)}>取消流程</Btn>}
        {open && confirmCancel && (
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span style={muted}>这一段还没交回的人会收到取消通知, 后面的段不再发。</span>
            <Btn kind="ghost" onClick={() => setConfirmCancel(false)}>算了</Btn>
            <Btn kind="primary" disabled={busy} onClick={() => run(async () => { await pipelineCancel(pipelineId); setConfirmCancel(false); return "流程已取消"; })}>确定取消</Btn>
          </div>
        )}
      </div>
    </Overlay>
  );
}
