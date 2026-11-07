/**
 * 发起人看任务进度 (11/4, docs/COLLEAGUE-TASK-PLAN.md)。
 *
 * 每位同事一行: 等回应 / 在做 / 已完成 / 逾期完成 / 不接 (原因) / 超时; 交回的结果直接展开看。
 * 「催一下」只催还没交回的; 「取消任务」通知还没交回的人别做了 (已交回的结果留着)。
 * 「让小鲶汇总」开工作台对话, 全在本机。
 * 定时 / 周期的 (11/4 第二步): 每人每期的结果按期列出; 「修改任务」改内容或时间, 发给还在参与的人重新同意。
 * 11/07: 「要交」单独一行; 「🐟 小鲶核对」按「要交」逐人看交回的结果, 每人下面一行「对上了 / 缺什么」,
 *        「请他重做」的框里先填好小鲶起草的话 (可以改); 「延期」只改截止, 对方不用重新同意。
 */
import { useCallback, useEffect, useState } from "react";

import type { ScheduleValue } from "./TaskSchedulePicker";
import TaskSchedulePicker from "./TaskSchedulePicker";

import { refreshTasks } from "../../lib/colleagueTasksStore";
import { summaryChatKey, summaryPrompt } from "../../lib/taskPrompts";
import {
  taskAdd, taskCancel, taskCheck, taskExtend, taskGet, taskRedo, taskRemind, taskRoomAutoPost, taskSend, taskUpdate,
  type OwnerRow, type TaskCheck, type TaskDetail,
} from "../../lib/tauri_colleague_tasks";
import { useUIStore } from "../../store/ui";
import { Overlay, StateTag, muted } from "./formsUi";
import { Btn, ErrorLine, inputStyle } from "./roomLinkUi";
import { ExpectLine, when } from "./TaskWork";

type Owner = Extract<TaskDetail, { role: "owner" }>;

const UNFINISHED = new Set(["等回应", "在做", "超时未回", "超时未交", "等重新同意"]);
const DONE = new Set(["已完成", "逾期完成"]);

/** 这个人的结果小鲶核对过没有、核对后有没有改了再交 */
export function checkOf(check: TaskCheck | undefined, r: OwnerRow) {
  const c = check?.people[r.email];
  if (!c || !r.result || !DONE.has(r.state)) return null;
  return { ...c, stale: !!c.result_at && !!r.done_at && c.result_at !== r.done_at };
}

/** datetime-local 的值 (本机时间) */
const localValue = (d: Date) => {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};

export default function TaskProgress({ taskId, onClose, initialError }: {
  taskId: string;
  onClose: () => void;
  initialError?: string;
}) {
  const [d, setD] = useState<Owner | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(initialError ?? null);
  const [info, setInfo] = useState<string | null>(null);
  const [more, setMore] = useState("");
  const [remindText, setRemindText] = useState("");
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [editing, setEditing] = useState(false);
  const [redoFor, setRedoFor] = useState<string | null>(null);
  const [redoNote, setRedoNote] = useState("");
  const [editText, setEditText] = useState("");
  const [editWhen, setEditWhen] = useState<ScheduleValue>({ schedule: null, problem: null });
  const [checking, setChecking] = useState(false);
  const [extendTo, setExtendTo] = useState<string | null>(null);
  const onEditWhen = useCallback((v: ScheduleValue) => setEditWhen(v), []);
  const startChat = useUIStore((s) => s.startEmailChat);

  const load = useCallback(async () => {
    await refreshTasks();   // 先取一次邮筒, 刚交回的也算上
    const r = await taskGet(taskId);
    if (r.role === "owner") setD(r);
  }, [taskId]);

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
    return <Overlay title="任务进度" onClose={onClose}>{error ? <ErrorLine>{error}</ErrorLine> : <span style={muted}>读取中…</span>}</Overlay>;
  }
  const { task, progress } = d;
  const live = task.status !== "cancelled";
  const unsent = progress.rows.filter((r) => r.state === "未发").length;
  const waiting = progress.rows.filter((r) => UNFINISHED.has(r.state)).length;
  const anyDone = progress.rows.some((r) => r.result || r.periods.length);
  const canCheck = !task.schedule && progress.rows.some((r) => r.result && DONE.has(r.state));
  const checkNow = () => {
    setChecking(true);
    void run(async () => {
      try {
        const r = await taskCheck(taskId);
        return `小鲶核对了 ${r.checked.length} 人${r.check.summary ? `: ${r.check.summary}` : ""}`;
      } finally {
        setChecking(false);
      }
    });
  };
  const failedNote = (failed: { to: string }[]) => (failed.length ? `; ${failed.length} 人没发出去: ${failed.map((f) => f.to).join("、")}` : "");

  return (
    <Overlay title={`「${task.title}」的进度`} onClose={onClose} width={760}>
      <span style={muted}>
        {task.status === "cancelled" ? "已取消" : task.status === "draft" ? "还没发出去" : "进行中"}
        {d.schedule_text ? ` · ⏰ ${d.schedule_text}` : task.deadline ? ` · 截止 ${when(task.deadline)}` : " · 不限时"} ·
        {" "}{Object.entries(progress.counts).map(([k, v]) => `${k} ${v}`).join(" · ") || "还没加人"}
      </span>
      <div style={{ fontSize: 13, whiteSpace: "pre-wrap", padding: "8px 10px", borderRadius: 4,
                    background: "var(--catfish-bg-subtle, rgba(0,0,0,0.04))" }}>{task.text}</div>
      {task.expect && <ExpectLine text={task.expect} />}
      {task.room_id && task.schedule && live && (
        <label style={{ fontSize: 13, display: "flex", gap: 6, alignItems: "center", cursor: "pointer" }}>
          <input type="checkbox" checked={!!task.room_auto_post} disabled={busy}
                 onChange={(e) => {
                   const on = e.target.checked;
                   void run(async () => { await taskRoomAutoPost(taskId, on); return on ? "以后每次结果会自动发到群里" : "关了, 以后的结果不再自动发到群里"; });
                 }} />
          每次结果自动发到群里 <span style={muted}>(你的电脑收到结果时自动转进群, 不用每次点; 随时能关)</span>
        </label>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {progress.rows.map((r) => (
          <div key={r.email} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 2fr", gap: 8, fontSize: 13, alignItems: "baseline" }}>
              <span>{r.email}</span>
              <StateTag state={r.state} />
              <span style={muted}>
                {r.done_at ? `交回于 ${when(r.done_at)}` : r.accepted_at ? `${when(r.accepted_at)} 接下` : ""}
                {r.reason ? ` · ${r.reason}` : ""}{r.reminded_at && !r.done_at ? " · 催过" : ""}
              </span>
            </div>
            {[...r.periods].reverse().map((p) => (
              <div key={p.period} style={{ fontSize: 13, whiteSpace: "pre-wrap", maxHeight: 220, overflow: "auto", padding: "6px 8px",
                                           borderLeft: "2px solid var(--status-ok, #16a34a)", background: "var(--catfish-bg-subtle, rgba(0,0,0,0.04))" }}>
                <div style={muted}>
                  第 {p.period} 次{p.at ? ` · ${when(p.at)}` : ""}{p.late_minutes ? ` · 晚了 ${Math.round(p.late_minutes / 60) || 1} 小时左右` : ""}
                  {p.waiting ? <span style={{ color: "var(--status-warn, #d97706)" }}> · 等对方确认 (还差 {p.waiting} 步)</span> : null}
                </div>
                {p.result ?? ""}
              </div>
            ))}
            {r.result && (
              <div style={{ fontSize: 13, whiteSpace: "pre-wrap", maxHeight: 220, overflow: "auto", padding: "6px 8px",
                            borderLeft: "2px solid var(--status-ok, #16a34a)", background: "var(--catfish-bg-subtle, rgba(0,0,0,0.04))" }}>
                {r.result}
              </div>
            )}
            <CheckLine c={checkOf(task.check, r)} />
            {task.status === "open" && !task.schedule && ["已完成", "逾期完成", "不接"].includes(r.state) && redoFor !== r.email && (
              <div><Btn kind="ghost" disabled={busy} onClick={() => { setRedoFor(r.email); setRedoNote(checkOf(task.check, r)?.redo_note ?? ""); }}>
                {r.state === "不接" ? "再请他一次" : checkOf(task.check, r)?.redo_note ? "按小鲶的意见请他重做" : "请他重做"}
              </Btn></div>
            )}
            {redoFor === r.email && (
              <div style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 6 }}>
                <input value={redoNote} onChange={(e) => setRedoNote(e.target.value)} placeholder="说一下要怎么改 (会发给他)" style={inputStyle} />
                <Btn kind="ghost" onClick={() => setRedoFor(null)}>算了</Btn>
                <Btn kind="primary" disabled={busy || !redoNote.trim()} onClick={() => run(async () => {
                  await taskRedo(taskId, r.email, redoNote);
                  setRedoFor(null);
                  return `已请 ${r.email} 重做`;
                })}>发给他</Btn>
              </div>
            )}
          </div>
        ))}
      </div>
      {progress.rows.some((r) => r.state === "等回应") && (
        <span style={muted}>「等回应」= 对方还没打开。对方要 Companion 1.2.56 以上才收得到; 版本旧的请他先升级。</span>
      )}

      {live && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 8 }}>
          <input value={more} onChange={(e) => setMore(e.target.value)} placeholder="再加人: 邮箱, 多个用逗号隔开" style={inputStyle} />
          <Btn kind="ghost" disabled={busy || !more.trim()} onClick={() => run(async () => {
            await taskAdd(taskId, more.split(/[\s,;，；]+/).filter(Boolean));
            setMore("");
            const r = await taskSend(taskId);
            return `发给新加的 ${r.sent.length} 人${failedNote(r.failed)}`;
          })}>加人并发出</Btn>
        </div>
      )}
      {task.status === "open" && !editing && (
        <div><Btn kind="ghost" disabled={busy} onClick={() => { setEditText(task.text); setEditing(true); }}>修改任务</Btn></div>
      )}
      {editing && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: 8, border: "1px solid var(--catfish-border)", borderRadius: 6 }}>
          <textarea value={editText} onChange={(e) => setEditText(e.target.value)} rows={3} style={{ ...inputStyle, resize: "vertical" }}
                    aria-label="改任务内容" />
          <TaskSchedulePicker initial={task.schedule ?? null} onChange={onEditWhen} />
          {editWhen.problem && <span style={{ ...muted, color: "var(--status-warn, #d97706)" }}>{editWhen.problem}</span>}
          <span style={muted}>改了会重新发给还在参与的人, 他们要重新同意 (定时先停); 已交回的结果留着。</span>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <Btn kind="ghost" onClick={() => setEditing(false)}>算了</Btn>
            <Btn kind="primary" disabled={busy || !editText.trim() || !!editWhen.problem} onClick={() => run(async () => {
              const r = await taskUpdate(taskId, { text: editText, schedule: editWhen.schedule });
              setEditing(false);
              return `改好了, 重新发给 ${r.sent.length} 人${failedNote(r.failed)}`;
            })}>改了重新发</Btn>
          </div>
        </div>
      )}
      {live && unsent > 0 && (
        <div><Btn kind="primary" disabled={busy} onClick={() => run(async () => {
          const r = await taskSend(taskId);
          return `发出去 ${r.sent.length} 人${failedNote(r.failed)}`;
        })}>发给没发出去的人 ({unsent})</Btn></div>
      )}
      {task.status === "open" && !task.schedule && waiting > 0 && extendTo === null && (
        <div><Btn kind="ghost" disabled={busy} onClick={() => {
          const base = new Date(Math.max(Date.now(), task.deadline ? new Date(task.deadline.length === 10 ? `${task.deadline}T18:00` : task.deadline).getTime() : 0));
          base.setDate(base.getDate() + 1);
          base.setHours(18, 0, 0, 0);
          setExtendTo(localValue(base));
        }}>延期</Btn></div>
      )}
      {extendTo !== null && (
        <div style={{ display: "grid", gridTemplateColumns: "auto 1fr auto auto", gap: 8, alignItems: "center" }}>
          <span style={{ fontSize: 13 }}>截止改到</span>
          <input type="datetime-local" value={extendTo} onChange={(e) => setExtendTo(e.target.value)} style={inputStyle} aria-label="新的截止时间" />
          <Btn kind="ghost" onClick={() => setExtendTo(null)}>算了</Btn>
          <Btn kind="primary" disabled={busy || !extendTo} onClick={() => run(async () => {
            const r = await taskExtend(taskId, extendTo);
            setExtendTo(null);
            return `截止改到 ${when(r.deadline)}, 通知了还没交的 ${r.sent.length} 人 (不用重新同意)${failedNote(r.failed)}${r.room_notice ? `; ${r.room_notice}` : ""}`;
          })}>延期并通知</Btn>
        </div>
      )}
      {task.status === "open" && waiting > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 8 }}>
          <input value={remindText} onChange={(e) => setRemindText(e.target.value)} placeholder="催一下的附言 (可不填)" style={inputStyle} />
          <Btn kind="ghost" disabled={busy} onClick={() => run(async () => {
            const r = await taskRemind(taskId, remindText);
            setRemindText("");
            return `催了 ${r.reminded.length} 人${failedNote(r.failed)}`;
          })}>催一下 ({waiting})</Btn>
        </div>
      )}

      {error && <ErrorLine>{error}</ErrorLine>}
      {info && <div style={{ fontSize: 12, color: "var(--status-ok, #16a34a)" }}>{info}</div>}

      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <span style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {canCheck && (
            <span title={task.expect ? "按「要交」逐人看交回的结果, 标出缺什么, 起草重做意见" : "按任务原话逐人看交回的结果 (派的时候写了「要交」会更准)"}>
              <Btn kind="ghost" disabled={busy} onClick={checkNow}>
                {checking ? "小鲶在核对… (一两分钟)" : task.check ? "🐟 再核对一次" : "🐟 小鲶核对结果"}
              </Btn>
            </span>
          )}
          <Btn kind="ghost" disabled={busy || !anyDone} onClick={() => { startChat(summaryChatKey(taskId), summaryPrompt(task)); onClose(); }}>
            🐟 让小鲶汇总
          </Btn>
          {task.check && !checking && <span style={muted}>核对于 {when(task.check.at)}{task.check.summary ? ` · ${task.check.summary}` : ""}</span>}
        </span>
        {task.status === "open" && !confirmCancel && <Btn kind="ghost" disabled={busy} onClick={() => setConfirmCancel(true)}>取消任务</Btn>}
        {task.status === "open" && confirmCancel && (
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span style={muted}>通知还没交回的 {waiting} 人别做了? 已交回的结果留着。</span>
            <Btn kind="ghost" onClick={() => setConfirmCancel(false)}>算了</Btn>
            <Btn kind="primary" disabled={busy} onClick={() => run(async () => {
              const r = await taskCancel(taskId);
              setConfirmCancel(false);
              return `已取消${failedNote(r.failed)}`;
            })}>确定取消</Btn>
          </div>
        )}
      </div>
    </Overlay>
  );
}

/** 小鲶核对的结论: 对上了 / 缺什么 (结果改过的标出来, 请他再核对) */
function CheckLine({ c }: { c: ReturnType<typeof checkOf> }) {
  if (!c) return null;
  const color = c.ok ? "var(--status-ok, #16a34a)" : "var(--status-warn, #d97706)";
  return (
    <div style={{ fontSize: 13, color, display: "flex", flexDirection: "column", gap: 2 }}>
      <span>
        🐟 {c.ok ? "对上了要求" : `还缺 ${c.gaps.length} 处`}
        {c.stale && <span style={muted}> · 核对后他又改了再交, 再核对一次</span>}
      </span>
      {!c.ok && c.gaps.map((g, i) => <span key={i} style={{ paddingLeft: 18 }}>· {g}</span>)}
    </div>
  );
}
