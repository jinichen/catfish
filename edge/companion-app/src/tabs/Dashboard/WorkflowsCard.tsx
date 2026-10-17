/**
 * 设置 → 技能 →「🔗 工作流」(10/17, docs/WORKFLOW-SKILL-PLAN.md §4.3 §5)。
 *
 * 主路是跟小鲶说 (「每周一帮我把…理一下」), 小鲶出草稿; 这里看 / 改 / 试跑 / 保存, 看运行记录, 失败的从那一步重跑。
 * 「对外步骤不用每次确认」开关只在这里 —— 小鲶不能替员工开。
 */
import { useCallback, useEffect, useState } from "react";

import {
  workflowCatalog, workflowDraftDelete, workflowDraftGet, workflowDraftSave, workflowGet, workflowList, workflowResume,
  workflowRun, workflowRuns, workflowSave, workflowSetAutoConfirm, workflowTrial, workflowUpdate,
  type DraftSummary, type RunResult, type RunSummary, type Workflow, type WorkflowSummary,
} from "../../lib/tauri_workflows";
import { refreshPending } from "../../lib/workflowStore";
import { Overlay, muted } from "../Collab/formsUi";
import { Btn, ErrorLine, cardStyle, inputStyle, itemStyle } from "../Collab/roomLinkUi";
import WorkflowEditor, { type Catalog } from "./WorkflowEditor";

const STATUS_COLOR: Record<string, string> = {
  done: "var(--status-ok, #16a34a)", failed: "var(--status-error, #dc2626)", invalid: "var(--status-error, #dc2626)",
  waiting_confirm: "var(--status-warn, #d97706)", declined: "var(--catfish-text-muted)", running: "var(--catfish-cyan)",
};
const STATUS_TEXT: Record<string, string> = {
  done: "跑完了", failed: "没做成", waiting_confirm: "等你确认", declined: "没发", running: "进行中", invalid: "没开跑",
};

function RunLine({ r, onResume }: { r: RunSummary; onResume: () => void }) {
  return (
    <div style={{ ...itemStyle, gap: 2 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
        <span style={{ fontSize: 13 }}>{r.title}</span>
        <span style={{ fontSize: 11, color: STATUS_COLOR[r.status] ?? "var(--catfish-text-muted)" }}>{STATUS_TEXT[r.status] ?? r.status}</span>
      </div>
      <span style={muted}>
        {r.created_at?.slice(0, 16).replace("T", " ")}
        {r.status === "failed" && r.failed_step ? ` · 停在 ${r.failed_step}: ${r.error ?? ""}` : ""}
        {r.status === "waiting_confirm" && r.pending ? ` · 停在「${r.pending.label}」, 页面顶上点确认` : ""}
      </span>
      {r.status === "failed" && <div><Btn kind="ghost" onClick={onResume}>从这一步重跑</Btn></div>}
    </div>
  );
}

function resultText(r: RunResult): string {
  if (r.status === "invalid") return r.error ?? "没开跑";
  return r.summary ?? r.error ?? r.status;
}

function Detail({ target, onClose, onChanged }: {
  target: { kind: "workflow"; skillPath: string } | { kind: "draft"; name: string };
  onClose: () => void;
  onChanged: () => void;
}) {
  const [cat, setCat] = useState<Catalog | null>(null);
  const [wf, setWf] = useState<Workflow | null>(null);
  const [meta, setMeta] = useState<{ description: string; triggers: string[]; namespace?: string; auto?: boolean }>({ description: "", triggers: [] });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [runParams, setRunParams] = useState<Record<string, string>>({});

  useEffect(() => {
    void (async () => {
      try {
        setCat(await workflowCatalog());
        if (target.kind === "workflow") {
          const g = await workflowGet(target.skillPath);
          setWf(g.workflow);
          setMeta({ description: g.description, triggers: g.triggers, auto: g.auto_confirm_outward });
        } else {
          const d = await workflowDraftGet(target.name);
          setWf(d.workflow);
          setMeta({ description: d.description, triggers: d.triggers, namespace: d.namespace });
        }
      } catch (e) {
        setMsg(e instanceof Error ? e.message : String(e));
      }
    })();
  }, [target]);

  async function act(fn: () => Promise<string | void>) {
    setBusy(true);
    setMsg(null);
    setProblems([]);
    try {
      const m = await fn();
      if (m) setMsg(m);
      onChanged();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const persist = async (): Promise<boolean> => {
    if (!wf) return false;
    const r = target.kind === "workflow"
      ? await workflowUpdate(target.skillPath, wf)
      : await workflowDraftSave({ name: target.name, namespace: meta.namespace ?? "personal", description: meta.description,
                                  triggers: meta.triggers, workflow: wf });
    setProblems(r.problems ?? []);
    return r.ok;
  };
  const params = Object.fromEntries(Object.entries(runParams).filter(([, v]) => v !== ""));
  const hasOutward = !!wf && !!cat && wf.steps.some((s) => s.confirm || cat.actions.find((a) => a.name === s.action)?.level === "outward");
  const title = target.kind === "workflow" ? target.skillPath : `草稿 · ${target.name}`;

  return (
    <Overlay title={title} onClose={onClose} width={820}>
      {!wf || !cat ? (msg ? <ErrorLine>{msg}</ErrorLine> : <span style={muted}>读取中…</span>) : (
        <>
          <span style={muted}>{meta.description}</span>
          <WorkflowEditor wf={wf} cat={cat} onChange={setWf} />
          {problems.length > 0 && <ErrorLine>{problems.join("; ")}</ErrorLine>}
          {Object.keys(wf.params ?? {}).length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <strong style={{ fontSize: 13 }}>这次跑用的参数 (空着就用默认)</strong>
              {Object.entries(wf.params ?? {}).map(([k, p]) => (
                <input key={k} value={runParams[k] ?? ""} placeholder={`${p.label || k} (默认 ${p.default ?? "无"})`}
                       onChange={(e) => setRunParams({ ...runParams, [k]: e.target.value })} style={inputStyle} />
              ))}
            </div>
          )}
          {target.kind === "workflow" && hasOutward && (
            <label style={{ fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}>
              <input type="checkbox" checked={!!meta.auto} disabled={busy}
                     onChange={(e) => void act(async () => {
                       await workflowSetAutoConfirm(target.skillPath, e.target.checked);
                       setMeta({ ...meta, auto: e.target.checked });
                       return e.target.checked ? "以后这个工作流的对外步骤直接发 (包括定时跑的)" : "以后对外步骤每次都停下等你确认";
                     })} />
              对外步骤不用每次确认 (定时跑也直接发)。你自己勾的「这步要我确认」不受影响。
            </label>
          )}
          {msg && <div style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>{msg}</div>}
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", flexWrap: "wrap" }}>
            {target.kind === "draft" ? (
              <>
                <Btn kind="ghost" disabled={busy} onClick={() => void act(async () => {
                  await workflowDraftDelete(target.name);
                  onClose();
                })}>删除草稿</Btn>
                <Btn kind="ghost" disabled={busy} onClick={() => void act(async () => ((await persist()) ? "草稿存好了" : "还有问题, 见上面"))}>存草稿</Btn>
                <Btn kind="ghost" disabled={busy} onClick={() => void act(async () => {
                  if (!(await persist())) return "还有问题, 见上面";
                  const r = await workflowTrial(target.name, params);
                  void refreshPending();
                  return `试跑: ${resultText(r)}`;
                })}>试跑</Btn>
                <Btn kind="primary" disabled={busy} onClick={() => void act(async () => {
                  if (!(await persist())) return "还有问题, 见上面";
                  const r = await workflowSave(target.name);
                  if (!r.ok) {
                    setProblems(r.problems ?? []);
                    return r.error ?? "保存失败";
                  }
                  onClose();
                })}>保存为工作流</Btn>
              </>
            ) : (
              <>
                <Btn kind="ghost" disabled={busy} onClick={() => void act(async () => ((await persist()) ? "改好了" : "还有问题, 见上面"))}>保存修改</Btn>
                <Btn kind="primary" disabled={busy} onClick={() => void act(async () => {
                  const r = await workflowRun(target.skillPath, params);
                  void refreshPending();
                  return resultText(r);
                })}>{busy ? "跑着…" : "现在跑一次"}</Btn>
              </>
            )}
          </div>
        </>
      )}
    </Overlay>
  );
}

export default function WorkflowsCard() {
  const [list, setList] = useState<{ workflows: WorkflowSummary[]; drafts: DraftSummary[] }>({ workflows: [], drafts: [] });
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [open, setOpen] = useState<{ kind: "workflow"; skillPath: string } | { kind: "draft"; name: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [l, r] = await Promise.all([workflowList(), workflowRuns(20)]);
      setList(l);
      setRuns(r.runs);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  return (
    <div style={{ ...cardStyle, height: "auto" }}>
      <div>
        <h3 style={{ margin: 0, fontSize: "var(--text-md)", fontWeight: 600 }}>🔗 工作流</h3>
        <div style={{ ...muted, marginTop: 4 }}>
          把反复做的事串成一条: 跟小鲶说「每周一帮我把带"资质"的邮件附件理一下, 建个任务」, 它会用鲶鱼会的能力和你教过的技能搭好草稿,
          你在这里看、改、试跑、保存。对外的步骤 (发收集表、推送到微信、发布知识库) 跑到时会停下, 在页面顶上点确认才发。
        </div>
      </div>
      {error && <ErrorLine>{error}</ErrorLine>}
      {note && <span style={muted}>{note}</span>}

      <strong style={{ fontSize: 13 }}>我的工作流</strong>
      {list.workflows.length === 0 && <span style={muted}>还没有。跟小鲶说说你反复在做的事。</span>}
      {list.workflows.map((w) => (
        <button key={w.skill_path} type="button" onClick={() => setOpen({ kind: "workflow", skillPath: w.skill_path })}
                style={{ ...itemStyle, textAlign: "left", cursor: "pointer", background: "transparent", color: "inherit", font: "inherit" }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>{w.skill_path}</span>
          <span style={muted}>{w.description} · {w.steps} 步{w.has_outward ? (w.auto_confirm_outward ? " · 对外步骤直接发" : " · 对外步骤会停下等确认") : ""}</span>
        </button>
      ))}

      {list.drafts.length > 0 && <strong style={{ fontSize: 13 }}>草稿 (还没保存)</strong>}
      {list.drafts.map((d) => (
        <button key={d.name} type="button" onClick={() => setOpen({ kind: "draft", name: d.name })}
                style={{ ...itemStyle, textAlign: "left", cursor: "pointer", background: "transparent", color: "inherit", font: "inherit" }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>{d.name}</span>
          <span style={muted}>{d.description} · {d.steps} 步 · {d.saved_at?.slice(0, 16).replace("T", " ")}</span>
        </button>
      ))}

      <strong style={{ fontSize: 13 }}>最近运行</strong>
      {runs.length === 0 && <span style={muted}>还没跑过。</span>}
      {runs.map((r) => (
        <RunLine key={r.run_id} r={r} onResume={() => void (async () => {
          try {
            const res = await workflowResume(r.run_id);
            setNote(resultText(res));
            void refreshPending();
          } catch (e) {
            setNote(e instanceof Error ? e.message : String(e));
          }
          void load();
        })()} />
      ))}

      {open && <Detail target={open} onClose={() => { setOpen(null); void load(); }} onChanged={() => void load()} />}
    </div>
  );
}
