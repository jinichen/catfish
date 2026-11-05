/**
 * 同事派给我的任务 (11/4, docs/COLLEAGUE-TASK-PLAN.md)。
 *
 * 先决定接不接: 「接下, 让小鲶做」开一个工作台对话 (工具审批 / 对外确认照常弹), 小鲶做完把结果存成草稿;
 * 「接下, 我自己做」只告诉对方在做了。结果框里核对 / 改好, 点「交回」才发出去 (只发这段结果);
 * 交回后还能改了再交, 后交的覆盖。也可以「不接」并写原因。
 * 定时 / 周期的 (11/4 第二步) 换成 TaskWorkScheduled: 接下 = 本机建定时, 每期一个框核对交回。
 */
import { useEffect, useState } from "react";

import { refreshTasks } from "../../lib/colleagueTasksStore";
import { doChatKey, doPrompt } from "../../lib/taskPrompts";
import {
  taskAccept, taskDecline, taskDraft, taskGet, taskSubmit,
  type IncomingState, type IncomingTask,
} from "../../lib/tauri_colleague_tasks";
import { useUIStore } from "../../store/ui";
import { Overlay, StateTag, muted } from "./formsUi";
import { Btn, ErrorLine, inputStyle } from "./roomLinkUi";
import MailBox from "./TaskMailBox";
import TaskWorkScheduled from "./TaskWorkScheduled";

export const when = (iso?: string | null) => (iso ? iso.slice(0, 16).replace("T", " ") : "");

export default function TaskWork({ taskId, onClose }: { taskId: string; onClose: () => void }) {
  const [rec, setRec] = useState<IncomingTask | null>(null);
  const [state, setState] = useState<IncomingState | "">("");
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [declining, setDeclining] = useState(false);
  const [reason, setReason] = useState("");
  const [extra, setExtra] = useState<{ scheduleText?: string; nextRun?: string | null }>({});
  const startChat = useUIStore((s) => s.startEmailChat);
  const openEmailDraft = useUIStore((s) => s.openEmailDraft);

  const load = async () => {
    const d = await taskGet(taskId);
    if (d.role !== "recipient") return;
    setRec(d.record);
    setState(d.state);
    setExtra({ scheduleText: d.schedule_text, nextRun: d.next_run });
    return d.record;
  };

  useEffect(() => {
    load().then((r) => r && setResult(r.draft?.result ?? r.done?.result ?? ""))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  if (!rec) {
    return <Overlay title="同事的任务" onClose={onClose}>{error ? <ErrorLine>{error}</ErrorLine> : <span style={muted}>读取中…</span>}</Overlay>;
  }
  const cancelled = rec.status === "cancelled";
  const accepted = !!rec.accepted_at && !rec.declined;

  async function run(fn: () => Promise<string | void>) {
    setBusy(true);
    setError(null);
    try {
      const msg = await fn();
      if (msg) setDone(msg);
      await load();
      void refreshTasks();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const askXiaonian = () => run(async () => {
    if (!accepted) await taskAccept(taskId, false, false);   // 在对话里看着小鲶做: 不再另起后台
    if (result.trim()) await taskDraft(taskId, result).catch(() => {});
    startChat(doChatKey(taskId), doPrompt(rec));
    onClose();
  });

  return (
    <Overlay title={`「${rec.task.title}」`} onClose={onClose}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <span style={muted}>
          {rec.owner} 请你帮忙{rec.task.deadline ? ` · 截止 ${when(rec.task.deadline)}` : ""}
        </span>
        <StateTag state={state} />
      </div>
      {rec.task.room_auto_post && (
        <div style={{ fontSize: 13, color: "var(--status-warn, #d97706)" }}>
          ⚠️ {rec.owner} 设了「每次结果自动发到群里」: 你每次交回的结果, 群里的人都会看到。
        </div>
      )}
      {!rec.task.schedule && rec.done && (
        <div style={{ fontSize: 13, padding: "6px 10px", borderRadius: 4, border: "1px solid var(--status-ok, #16a34a)",
                      color: "var(--status-ok, #16a34a)" }}>
          ✅ 已交回给 {rec.owner} ({when(rec.done.at)}) —— 对方看到的是那一版。要更新就改下面的内容, 点「改了再交」。
        </div>
      )}
      <div style={{ fontSize: 13, whiteSpace: "pre-wrap", padding: "8px 10px", borderRadius: 4,
                    background: "var(--catfish-bg-subtle, rgba(0,0,0,0.04))" }}>{rec.task.text}</div>
      {rec.task.context && (
        <details open style={{ fontSize: 13 }}>
          <summary style={{ cursor: "pointer", ...muted }}>{rec.owner} 转来的上一段结果</summary>
          <div style={{ whiteSpace: "pre-wrap", maxHeight: 220, overflow: "auto", padding: "6px 8px", marginTop: 4,
                        borderLeft: "2px solid var(--catfish-cyan)" }}>{rec.task.context}</div>
        </details>
      )}
      {rec.redo_note && !rec.done && (
        <div style={{ fontSize: 13, color: "var(--status-warn, #d97706)" }}>对方请你重做: {rec.redo_note}</div>
      )}
      {rec.task.schedule && (
        <TaskWorkScheduled rec={rec} state={state} scheduleText={extra.scheduleText ?? ""} nextRun={extra.nextRun}
                           busy={busy} run={(fn) => void run(fn)} onClose={onClose} />
      )}
      {!rec.task.schedule && rec.reminded_at && !rec.done && (
        <div style={{ ...muted, color: "var(--status-warn, #d97706)" }}>对方催了{rec.remind_text ? `: ${rec.remind_text}` : ""}</div>
      )}
      {cancelled && <div style={muted}>对方已经取消了这个任务, 不用做了。</div>}
      {!rec.task.schedule && rec.declined && <div style={muted}>你回了不接: {rec.declined.reason}</div>}

      {!rec.task.schedule && !cancelled && !accepted && !rec.done && !declining && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {rec.declined && <span style={muted}>改主意了, 还能接下:</span>}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <Btn kind="primary" disabled={busy} onClick={() => run(async () => {
              const r = await taskAccept(taskId);
              return r.start_error ?? "接下了, 小鲶已经在后台开工, 做好了会通知你核对";
            })}>🐟 接下 (小鲶马上开工)</Btn>
            <Btn kind="ghost" disabled={busy} onClick={askXiaonian}>接下, 在对话里看着小鲶做</Btn>
            <Btn kind="ghost" disabled={busy} onClick={() => run(async () => { await taskAccept(taskId, false, false); return "已告诉对方你在做了"; })}>
              接下, 我自己做
            </Btn>
            {!rec.declined && <Btn kind="ghost" disabled={busy} onClick={() => setDeclining(true)}>不接</Btn>}
          </div>
          <span style={muted}>
            小鲶用你电脑上的材料做。后台做的时候发邮件、发消息、删东西这类一律不做, 写进结果里等你处理; 在对话里做的照常先问你。
            做完只存草稿, 交不交回你定。
          </span>
        </div>
      )}

      {!rec.task.schedule && !cancelled && accepted && !rec.done && rec.auto_started_at && !rec.draft?.result && (
        <div style={{ fontSize: 13, color: rec.start_failed ? "var(--status-warn, #d97706)" : "var(--catfish-cyan)" }}>
          {rec.start_failed
            ? "🐟 小鲶在后台没做出来 —— 点「让小鲶做」在对话里做, 或者自己写结果。"
            : `🐟 小鲶 ${when(rec.auto_started_at).slice(11)} 接下后已经在后台开工, 做好了会通知你核对。想看着它做就点「让小鲶做」。`}
        </div>
      )}
      {!rec.task.schedule && (rec.draft?.mail ?? rec.done?.mail) && (
        <MailBox mail={(rec.draft?.mail ?? rec.done?.mail)!} owner={rec.owner} sent={!!rec.done}
                 onOpen={(id) => { openEmailDraft(id); onClose(); }} />
      )}
      {!rec.task.schedule && !cancelled && (accepted || rec.done) && (
        <>
          <strong style={{ fontSize: 13 }}>交回给 {rec.owner} 的结果</strong>
          <textarea value={result} onChange={(e) => setResult(e.target.value)} rows={8}
                    placeholder="小鲶做完会把结果存在这里; 也可以自己写。点「交回」才发出去, 只发这一段。"
                    style={{ ...inputStyle, resize: "vertical" }} />
          {rec.draft && <span style={muted}>草稿存于 {when(rec.draft.updated_at)}</span>}
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
            <div style={{ display: "flex", gap: 8 }}>
              <Btn kind="ghost" disabled={busy} onClick={askXiaonian}>🐟 {rec.draft ? "再让小鲶做" : "让小鲶做"}</Btn>
              {!rec.done && <Btn kind="ghost" disabled={busy} onClick={() => setDeclining(true)}>不接了</Btn>}
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <Btn kind="ghost" disabled={busy} onClick={() => run(async () => {
                await taskDraft(taskId, result);
                return rec.done
                  ? `改动存成草稿了 —— ${rec.owner} 看到的还是 ${when(rec.done.at)} 交回的那版, 点「改了再交」才更新`
                  : "草稿存好了 (还没交回)";
              })}>
                存草稿
              </Btn>
              <Btn kind="primary" disabled={busy || !result.trim()} onClick={() => run(async () => {
                const r = await taskSubmit(taskId, result);
                const again = rec.done ? "重新交回了, 对方看到的换成这一版" : "交回了";
                return r.late ? `${again} (已过截止, 对方那边会标逾期)` : again;
              })}>{rec.done ? "改了再交" : "交回"}</Btn>
            </div>
          </div>
        </>
      )}

      {declining && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="说一句为什么不接 (会发给对方)" style={inputStyle} />
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <Btn kind="ghost" onClick={() => setDeclining(false)}>算了</Btn>
            <Btn kind="primary" disabled={busy || !reason.trim()} onClick={async () => {
              // 11/5 鸿波: 不接了就关掉弹窗, 不再停在一堆「接下」按钮上
              setBusy(true);
              setError(null);
              try {
                await taskDecline(taskId, reason);
                void refreshTasks();
                onClose();
              } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
                setBusy(false);
              }
            }}>告诉对方</Btn>
          </div>
        </div>
      )}

      {error && <ErrorLine>{error}</ErrorLine>}
      {done && <div style={{ fontSize: 12, color: "var(--status-ok, #16a34a)" }}>{done}</div>}
    </Overlay>
  );
}

