/**
 * 「协同」页左上: 请同事帮忙 (11/4 起是「派任务给多位同事」, docs/COLLEAGUE-TASK-PLAN.md 第一步)。
 *
 * 原来 (P50, 9/10) 只能找一个人、对方 10 分钟内要点同意、你这台电脑要一直开着直连对方 ——
 * 换成跟一起填表同一个邮筒: 发出去就不用管, 对方开机再处理, 在他自己的电脑上做完交回。
 * 上: 同事派给我的 (要我动手的排前面); 下: 我发出的 (看进度)。点一行打开对应的弹层。
 * 11/5 鸿波: 列表太长、一条占两行、找不到 —— 改成一行一条, 默认只看「要处理」的 (结束的收起来), 顶上能搜标题 / 同事。
 */
import { useEffect, useState, type ReactNode } from "react";

import { FlowArrow } from "@phosphor-icons/react";

import { Ico } from "../../components/icons";
import { useColleagueTasks } from "../../lib/colleagueTasksStore";
import { workText } from "../../lib/taskWorkProgress";
import { useUIStore } from "../../store/ui";
import { StateTag, muted } from "./formsUi";
import PipelineComposer from "./PipelineComposer";
import PipelineProgress from "./PipelineProgress";
import { Btn, CardHead, cardStyle, chipStyle, inputStyle, itemStyle } from "./roomLinkUi";
import TaskComposer from "./TaskComposer";
import TaskProgress from "./TaskProgress";
import TaskWork from "./TaskWork";

const STATUS_LABEL = { draft: "没发出", open: "进行中", cancelled: "已取消" } as const;
const ACTIVE = new Set(["待处理", "在做", "待核对", "待确认", "要重新同意", "没做成", "定时没了"]);

const rowButton = { ...itemStyle, textAlign: "left" as const, cursor: "pointer", background: "transparent", color: "inherit", font: "inherit",
                    padding: "8px 12px", flexDirection: "row" as const, alignItems: "center", gap: 8 };

/** 一行一条: 标题 (放不下省略) · 说明 · 状态 */
function Row({ title, sub, state, onClick }: { title: ReactNode; sub: string; state: ReactNode; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} style={rowButton} title={typeof title === "string" ? title : undefined}>
      <span style={{ flex: 1, minWidth: 0, fontSize: "var(--text-sm)", fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {title}
      </span>
      <span style={{ ...muted, maxWidth: "45%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={sub}>{sub}</span>
      <span style={{ flexShrink: 0 }}>{state}</span>
    </button>
  );
}

/** 搜标题 / 同事邮箱 (不分大小写) */
export function matches(q: string, ...fields: (string | null | undefined)[]): boolean {
  const k = q.trim().toLowerCase();
  return !k || fields.some((f) => (f ?? "").toLowerCase().includes(k));
}

const HOW = "一次发给多位同事, 各自决定接不接; 在他自己的电脑上由他的小鲶或本人做完, 核对后交回。工具和要发出去的东西都由对方本人放行, 结果只回到你这台电脑。中央只转交加密内容、看不到原文, 取走即清空, 不保存记录。";

/** 11/24: 一条都没有时, 列表区不留一大片空白 —— 说清楚怎么用 (跟标题旁说明同一套话) */
function EmptyHow() {
  const step = { display: "flex", gap: "var(--space-2)", fontSize: 13, lineHeight: 1.6 } as const;
  const num = { flex: "none", width: 20, color: "var(--catfish-text-muted)" } as const;
  return (
    <div style={{ margin: "auto 0", padding: "var(--space-6) var(--space-4)", display: "flex", flexDirection: "column", gap: "var(--space-3)",
                  maxWidth: 440, alignSelf: "center" }}>
      <strong style={{ fontSize: "var(--text-sm)" }}>还没有派出或收到的任务</strong>
      <div style={step}><span style={num}>1</span><span>点「派任务」, 写要做什么、截止、发给谁 (最多 20 人)。</span></div>
      <div style={step}><span style={num}>2</span><span>同事各自决定接不接, 在他自己的电脑上由他的小鲶或本人做完。工具和要发出去的东西都由对方本人放行。</span></div>
      <div style={step}><span style={num}>3</span><span>结果交回到你这台电脑, 你来核对。中央只转交加密内容、看不到原文。</span></div>
      <span style={muted}>要几个人接力做 (先收数据、再汇总), 用「多阶段」。同事派给你的任务也会出现在这里。</span>
    </div>
  );
}

const shortDay = (iso?: string | null) => (iso ? `${+iso.slice(5, 7)}/${+iso.slice(8, 10)}${iso.length > 10 ? ` ${iso.slice(11, 16)}` : ""}` : "");

export default function TasksPanel() {
  const { incoming, outgoing, pipelines = [], loaded, syncError } = useColleagueTasks();
  const [composing, setComposing] = useState(false);
  const [composingPipeline, setComposingPipeline] = useState(false);
  const [pipeline, setPipeline] = useState<string | null>(null);
  const [working, setWorking] = useState<string | null>(null);
  const [watching, setWatching] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | undefined>();
  const [q, setQ] = useState("");
  const [all, setAll] = useState(false);
  // 11/07 提醒条上点「看任务」「去交回」过来的: 直接打开那个任务
  const focus = useUIStore((s) => s.pendingTaskFocus);
  useEffect(() => {
    if (!focus) return;
    const f = useUIStore.getState().consumeTaskFocus();
    if (f?.role === "owner") setWatching(f.taskId);
    else if (f) setWorking(f.taskId);
  }, [focus]);
  const inboxAll = [...incoming].sort((a, b) => Number(ACTIVE.has(b.state)) - Number(ACTIVE.has(a.state)))
    .filter((t) => matches(q, t.title, t.owner));
  const inbox = all ? inboxAll : inboxAll.filter((t) => ACTIVE.has(t.state));
  const outAll = outgoing.filter((t) => matches(q, t.title));
  const finished = (t: (typeof outgoing)[number]) => !t.schedule && t.recipients > 0
    && ["已完成", "逾期完成", "不接", "已取消"].reduce((n, k) => n + (t.progress[k as keyof typeof t.progress] ?? 0), 0) >= t.recipients;
  const outs = all ? outAll : outAll.filter((t) => t.status !== "cancelled" && !finished(t));
  const pipesAll = pipelines.filter((p) => matches(q, p.title));
  const pipes = all ? pipesAll : pipesAll.filter((p) => p.status === "open");
  const hidden = inboxAll.length - inbox.length + outAll.length - outs.length + pipesAll.length - pipes.length;
  const empty = loaded && !q && !hidden && inbox.length + outs.length + pipes.length === 0;

  return (
    <div style={{ ...cardStyle, minHeight: 0, overflow: "hidden" }}>
      <CardHead title="请同事帮忙"
                sub="各自决定接不接, 在自己电脑上做完交回; 结果只回到你这里, 中央看不到原文。"
                subTitle={HOW}
                aside={<>
                  <Btn kind="ghost" onClick={() => setComposingPipeline(true)}>多阶段</Btn>
                  <Btn kind="primary" onClick={() => setComposing(true)}>派任务</Btn>
                </>} />
      {syncError && <span style={muted}>暂时收不到同事的任务: {syncError}</span>}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜任务名 / 同事" style={{ ...inputStyle, flex: 1, padding: "4px 8px", minHeight: 28 }} />
        <button type="button" style={chipStyle(!all)} onClick={() => setAll(false)}>要处理的</button>
        <button type="button" style={chipStyle(all)} onClick={() => setAll(true)}>全部</button>
      </div>
      {!all && hidden > 0 && <span style={muted}>另有 {hidden} 条已结束 (交回 / 不接 / 取消), 点「全部」看。</span>}

      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", display: "flex", flexDirection: "column", gap: "var(--space-4)", paddingRight: 2 }}>
      {empty ? <EmptyHow /> : <>
      <section style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
        <strong style={{ fontSize: 13 }}>同事请我做的</strong>
        {loaded && inbox.length === 0 && <span style={muted}>{all || q ? "没有找到。" : "没有要你处理的。"}</span>}
        {inbox.map((t) => (
          <Row key={t.task_id} title={t.title} onClick={() => setWorking(t.task_id)} state={<StateTag state={t.state} />}
               sub={[t.state === "在做" && t.work !== undefined ? workText(t.work) : "",   // 11/07 小鲶做到哪了放最前 (行窄会截)
                     t.owner, t.schedule ? t.schedule : t.deadline ? `截止 ${shortDay(t.deadline)}` : "",
                     t.to_review ? `${t.to_review} 次待核对` : "", t.to_confirm ? `${t.to_confirm} 次要确认` : "",
                     t.late && ACTIVE.has(t.state) ? "已过截止" : "", t.reminded_at && ACTIVE.has(t.state) ? "被催了" : ""]
                     .filter(Boolean).join(" · ")} />
        ))}
      </section>

      <section style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
        <strong style={{ fontSize: 13 }}>我请同事做的</strong>
        {loaded && outs.length === 0 && pipes.length === 0 && <span style={muted}>{all || q ? "没有找到。" : "没有进行中的。"}</span>}
        {pipes.map((p) => (
          <Row key={p.pipeline_id} title={<><Ico as={FlowArrow} />{p.title}</>} onClick={() => { setNotice(undefined); setPipeline(p.pipeline_id); }}
               state={<StateTag state={p.status === "open" ? (p.waiting_release ? "待处理" : "进行中") : p.status === "done" ? "已完成" : "已取消"} />}
               sub={(p.status === "open" ? `第 ${p.current}/${p.stages} 段` : `${p.stages} 段`)
                    + (p.waiting_release ? " · 等你放行" : Object.keys(p.progress).length ? ` · ${Object.entries(p.progress).map(([k, v]) => `${k} ${v}`).join(" · ")}` : "")} />
        ))}
        {outs.map((t) => (
          <Row key={t.task_id} title={t.title} onClick={() => { setNotice(undefined); setWatching(t.task_id); }}
               state={<StateTag state={STATUS_LABEL[t.status]} />}
               sub={[`${t.recipients} 人`, t.schedule ? t.schedule : t.deadline ? `截止 ${shortDay(t.deadline)}` : "",
                     ...Object.entries(t.progress).map(([k, v]) => `${k} ${v}`)].filter(Boolean).join(" · ")} />
        ))}
      </section>
      </>}
      </div>

      {composing && <TaskComposer onClose={() => setComposing(false)}
                                  onCreated={(id, n) => { setComposing(false); setNotice(n); setWatching(id); }} />}
      {composingPipeline && <PipelineComposer onClose={() => setComposingPipeline(false)}
                                              onCreated={(id, n) => { setComposingPipeline(false); setNotice(n); setPipeline(id); }} />}
      {pipeline && <PipelineProgress pipelineId={pipeline} initialError={notice} onClose={() => { setPipeline(null); setNotice(undefined); }} />}
      {working && <TaskWork taskId={working} onClose={() => setWorking(null)} />}
      {watching && <TaskProgress taskId={watching} initialError={notice} onClose={() => { setWatching(null); setNotice(undefined); }} />}
    </div>
  );
}
