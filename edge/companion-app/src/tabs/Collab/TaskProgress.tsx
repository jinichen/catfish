/**
 * 发起人看任务进度 (11/4, docs/COLLEAGUE-TASK-PLAN.md)。
 *
 * 每位同事一行: 等回应 / 在做 / 已完成 / 逾期完成 / 不接 (原因) / 超时; 交回的结果直接展开看。
 * 「催一下」只催还没交回的; 「取消任务」通知还没交回的人别做了 (已交回的结果留着)。
 * 「让小鲶汇总」开工作台对话, 全在本机。
 */
import { useCallback, useEffect, useState } from "react";

import { refreshTasks } from "../../lib/colleagueTasksStore";
import { summaryChatKey, summaryPrompt } from "../../lib/taskPrompts";
import {
  taskAdd, taskCancel, taskGet, taskRemind, taskSend,
  type TaskDetail,
} from "../../lib/tauri_colleague_tasks";
import { useUIStore } from "../../store/ui";
import { Overlay, StateTag, muted } from "./formsUi";
import { Btn, ErrorLine, inputStyle } from "./roomLinkUi";
import { when } from "./TaskWork";

type Owner = Extract<TaskDetail, { role: "owner" }>;

const UNFINISHED = new Set(["等回应", "在做", "超时未回", "超时未交"]);

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
  const anyDone = progress.rows.some((r) => r.result);
  const failedNote = (failed: { to: string }[]) => (failed.length ? `; ${failed.length} 人没发出去: ${failed.map((f) => f.to).join("、")}` : "");

  return (
    <Overlay title={`「${task.title}」的进度`} onClose={onClose} width={760}>
      <span style={muted}>
        {task.status === "cancelled" ? "已取消" : task.status === "draft" ? "还没发出去" : "进行中"}
        {task.deadline ? ` · 截止 ${when(task.deadline)}` : " · 不限时"} ·
        {" "}{Object.entries(progress.counts).map(([k, v]) => `${k} ${v}`).join(" · ") || "还没加人"}
      </span>
      <div style={{ fontSize: 13, whiteSpace: "pre-wrap", padding: "8px 10px", borderRadius: 4,
                    background: "var(--catfish-bg-subtle, rgba(0,0,0,0.04))" }}>{task.text}</div>

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
            {r.result && (
              <div style={{ fontSize: 13, whiteSpace: "pre-wrap", maxHeight: 220, overflow: "auto", padding: "6px 8px",
                            borderLeft: "2px solid var(--status-ok, #16a34a)", background: "var(--catfish-bg-subtle, rgba(0,0,0,0.04))" }}>
                {r.result}
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
      {live && unsent > 0 && (
        <div><Btn kind="primary" disabled={busy} onClick={() => run(async () => {
          const r = await taskSend(taskId);
          return `发出去 ${r.sent.length} 人${failedNote(r.failed)}`;
        })}>发给没发出去的人 ({unsent})</Btn></div>
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
        <Btn kind="ghost" disabled={busy || !anyDone} onClick={() => { startChat(summaryChatKey(taskId), summaryPrompt(task)); onClose(); }}>
          🐟 让小鲶汇总
        </Btn>
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
