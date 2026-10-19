/**
 * 设置 → 技能 →「🔗 工作流」(10/17, docs/WORKFLOW-SKILL-PLAN.md §4.3 §5)。
 *
 * 主路是跟小鲶说 (「每周一帮我把…理一下」), 小鲶出草稿; 这里看 / 改 / 试跑 / 保存, 看运行记录, 失败的从那一步重跑。
 * 「对外步骤不用每次确认」开关只在这里 —— 小鲶不能替员工开。
 * 10/19: 卡片显示中文名 / 步骤链 / 参数 / 定时, 直接能「运行 / 编辑 / 定时」; 失败说人话 +「让小鲶看看」(零件在 WorkflowParts.tsx)。
 */
import { useCallback, useEffect, useState } from "react";

import { selectExpert } from "../../lib/expertChat";
import { backfillNamesOnce } from "../../lib/tauri_skill_names";
import { cronJobDelete, cronJobsList, type CronJob } from "../../lib/tauri_cron";
import {
  workflowCatalog, workflowDraftDelete, workflowDraftGet, workflowDraftSave, workflowGet, workflowList, workflowResume,
  workflowRun, workflowRunDelete, workflowRuns, workflowSave, workflowScheduleSet, workflowSetAutoConfirm, workflowTrial, workflowUpdate,
  type DraftSummary, type RunResult, type RunSummary, type Workflow, type WorkflowSummary,
} from "../../lib/tauri_workflows";
import { useUIStore } from "../../store/ui";
import { refreshPending } from "../../lib/workflowStore";
import { Overlay, muted } from "../Collab/formsUi";
import { Btn, ErrorLine, cardStyle, inputStyle, itemStyle } from "../Collab/roomLinkUi";
import WorkflowEditor, { type Catalog } from "./WorkflowEditor";
import { ParamsLine, RunLine, ScheduleDialog, StepChain, askXiaonianPrompt, cronText, localTime, useTwoClick } from "./WorkflowParts";

/** 这个工作流的定时任务 (我们建的任务提示里写着 skill_path="…") */
function schedulesOf(jobs: CronJob[], skillPath: string): CronJob[] {
  return jobs.filter((j) => (j.prompt ?? "").includes(`skill_path="${skillPath}"`));
}

const RECENT = 5;
/** 「最近运行」收起 / 展开记在本机 (读写不了就默认收起) */
const RUNS_OPEN_KEY = "catfish.workflows.runsOpen";
function readRunsOpen(): boolean {
  try { return localStorage.getItem(RUNS_OPEN_KEY) === "1"; } catch { return false; }
}
function writeRunsOpen(v: boolean) {
  try { localStorage.setItem(RUNS_OPEN_KEY, v ? "1" : "0"); } catch { /* 记不住就下次再点 */ }
}

function resultText(r: RunResult): string {
  if (r.status === "invalid") return r.error ?? "没开跑";
  return r.summary ?? r.error ?? r.status;
}

function Detail({ target, onClose, onChanged }: {
  target: { kind: "workflow"; skillPath: string; title?: string } | { kind: "draft"; name: string; title?: string };
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
  const title = target.kind === "workflow" ? (target.title || target.skillPath) : `草稿 · ${target.title || target.name}`;

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
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [open, setOpen] = useState<{ kind: "workflow"; skillPath: string; title?: string } | { kind: "draft"; name: string; title?: string } | null>(null);
  const [scheduling, setScheduling] = useState<WorkflowSummary | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [runsOpen, setRunsOpen] = useState(readRunsOpen);
  const [clearArmed, clickClear] = useTwoClick();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const startChat = useUIStore((s) => s.startEmailChat);

  const load = useCallback(async () => {
    try {
      const [l, r] = await Promise.all([workflowList(), workflowRuns(30)]);
      setList(l);
      setRuns(r.runs);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    // 定时任务读不到 (hermes 没起来) 不影响别的
    cronJobsList().then(setJobs).catch(() => setJobs([]));
  }, []);
  useEffect(() => {
    void load();
    void backfillNamesOnce().then((changed) => { if (changed) void load(); });
  }, [load]);

  const fail = (e: unknown) => setNote(e instanceof Error ? e.message : String(e));
  const askXiaonian = (r: RunSummary) => {
    selectExpert(null); // 让主小鲶看, 不是某个专家
    startChat(`wf-${r.run_id}`, askXiaonianPrompt(r));
  };
  const shownRuns = showAll ? runs : runs.slice(0, RECENT);
  const failedCount = runs.filter((r) => r.status === "failed").length;
  const waitingCount = runs.filter((r) => r.status === "waiting_confirm").length;
  const toggleRuns = () => { setRunsOpen(!runsOpen); writeRunsOpen(!runsOpen); };
  const deleteRuns = (ids: string[], allFinished = false) => void (async () => {
    try {
      const res = await workflowRunDelete(ids, allFinished);
      setNote(res.kept.length ? `删了 ${res.deleted.length} 条; ${res.kept.length} 条还在跑或等你确认, 留着` : null);
    } catch (e) {
      fail(e);
    }
    void load();
  })();

  return (
    <div style={{ ...cardStyle, height: "auto" }}>
      <div>
        <h3 style={{ margin: 0, fontSize: "var(--text-md)", fontWeight: 600 }}>🔗 工作流</h3>
        <div style={{ ...muted, marginTop: 4 }}>
          把反复做的事串成一条, 以后一句话或定时整条跑完。跟小鲶说「每周一帮我把带"资质"的邮件理一下, 建个任务」它就搭好草稿。
          带 🔔 的步骤是对外的 (发收集表、推送微信…), 跑到时会停下, 在页面顶上点确认才发。
        </div>
      </div>
      {error && <ErrorLine>{error}</ErrorLine>}
      {note && <span style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>{note}</span>}

      <strong style={{ fontSize: 13 }}>我的工作流</strong>
      {list.workflows.length === 0 && <span style={muted}>还没有。跟小鲶说说你反复在做的事。</span>}
      {list.workflows.map((w) => {
        const sched = schedulesOf(jobs, w.skill_path);
        return (
          <div key={w.skill_path} style={{ ...itemStyle, gap: 6 }}>
            <div>
              <span style={{ fontSize: 14, fontWeight: 600 }}>{w.title || w.skill_path}</span>
              <span style={{ ...muted, marginLeft: 8 }}>{w.skill_path}</span>
            </div>
            {w.summary && <span style={{ fontSize: 12 }}>{w.summary}</span>}
            {w.chain?.length > 0 && <StepChain chain={w.chain} />}
            <ParamsLine params={w.params ?? []} />
            <span style={muted}>
              {sched.length > 0 ? `⏰ ${sched.map((j) => cronText(j.schedule?.expr) || j.schedule_display || "已定时").join("、")}` : "没有定时"}
              {w.has_outward ? (w.auto_confirm_outward ? " · 🔔 对外步骤直接发" : " · 🔔 对外步骤会停下等你确认") : ""}
            </span>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <Btn kind="primary" disabled={!!busy} onClick={() => void (async () => {
                setBusy(w.skill_path);
                setNote(`正在跑「${w.title}」…`);
                try {
                  const r = await workflowRun(w.skill_path, {});
                  setNote(resultText(r));
                  void refreshPending();
                } catch (e) {
                  fail(e);
                } finally {
                  setBusy(null);
                  void load();
                }
              })()}>{busy === w.skill_path ? "跑着…" : "▶ 运行"}</Btn>
              <Btn kind="ghost" disabled={!!busy} onClick={() => setOpen({ kind: "workflow", skillPath: w.skill_path, title: w.title })}>编辑</Btn>
              {sched.length === 0 ? (
                <Btn kind="ghost" disabled={!!busy} onClick={() => setScheduling(w)}>⏰ 定时</Btn>
              ) : (
                <Btn kind="ghost" disabled={!!busy} onClick={() => void (async () => {
                  try {
                    for (const j of sched) await cronJobDelete(j.id);
                    setNote(`「${w.title}」的定时取消了`);
                  } catch (e) {
                    fail(e);
                  }
                  void load();
                })()}>取消定时</Btn>
              )}
            </div>
          </div>
        );
      })}

      {list.drafts.length > 0 && <strong style={{ fontSize: 13 }}>草稿 (还没保存)</strong>}
      {list.drafts.map((d) => (
        <button key={d.name} type="button" onClick={() => setOpen({ kind: "draft", name: d.name, title: d.title })}
                style={{ ...itemStyle, textAlign: "left", cursor: "pointer", background: "transparent", color: "inherit", font: "inherit" }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>{d.title || d.name}</span>
          <span style={muted}>{d.description} · {d.steps} 步 · {localTime(d.saved_at)}</span>
        </button>
      ))}

      <button type="button" onClick={toggleRuns} aria-expanded={runsOpen}
              style={{ display: "flex", gap: 8, alignItems: "baseline", background: "none", border: "none", padding: 0,
                       cursor: "pointer", color: "inherit", font: "inherit", textAlign: "left" }}>
        <strong style={{ fontSize: 13 }}>{runsOpen ? "▾" : "▸"} 最近运行</strong>
        <span style={muted}>
          {runs.length === 0 ? "还没跑过" : `${runs.length} 条`}
          {failedCount > 0 && ` · ${failedCount} 条没做成`}
          {waitingCount > 0 && ` · ${waitingCount} 条等你确认`}
        </span>
      </button>
      {runsOpen && shownRuns.map((r) => (
        <RunLine key={r.run_id} r={r} onAsk={() => askXiaonian(r)} onDelete={() => deleteRuns([r.run_id])} onResume={() => void (async () => {
          try {
            const res = await workflowResume(r.run_id);
            setNote(resultText(res));
            void refreshPending();
          } catch (e) {
            fail(e);
          }
          void load();
        })()} />
      ))}
      {runsOpen && runs.length > 0 && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {runs.length > RECENT && (
            <Btn kind="ghost" onClick={() => setShowAll(!showAll)}>{showAll ? "只看最近的" : `看更早的 (${runs.length - RECENT} 条)`}</Btn>
          )}
          <Btn kind="ghost" onClick={() => clickClear(() => deleteRuns([], true))}>
            {clearArmed ? "再点一次, 全部清掉" : "清空记录"}
          </Btn>
        </div>
      )}

      {open && <Detail target={open} onClose={() => { setOpen(null); void load(); }} onChanged={() => void load()} />}
      {scheduling && (
        <ScheduleDialog title={scheduling.title || scheduling.skill_path} onClose={() => setScheduling(null)}
                        onSave={async (cron) => {
                          await workflowScheduleSet(scheduling.skill_path, scheduling.title || scheduling.skill_path, cron);
                          setNote(`「${scheduling.title}」定好了: ${cronText(cron)}`);
                          void load();
                        }} />
      )}
    </div>
  );
}
