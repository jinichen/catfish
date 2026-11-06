/**
 * 派任务给同事 (11/4, docs/COLLEAGUE-TASK-PLAN.md 第一步)。
 *
 * 写要做什么 + 截止 + 发给谁 (最多 20 人) → 每人各自决定接不接, 接下后在**他自己的电脑上**由他的小鲶 (或他本人) 做,
 * 核对后交回; 结果只回到你这台电脑。也可以直接跟小鲶说「让张三、李四各整理一下本周拜访, 明天中午前」。
 * 11/4 第二步: 「什么时候做」可以选定时做一次 / 周期 (TaskSchedulePicker), 到点在同事电脑上由他的小鲶做。
 */
import { useCallback, useState } from "react";

import { refreshTasks } from "../../lib/colleagueTasksStore";
import { taskCreate, taskSend } from "../../lib/tauri_colleague_tasks";
import { Overlay, muted } from "./formsUi";
import RecipientPicker, { allRecipients } from "./RecipientPicker";
import TaskSchedulePicker, { type ScheduleValue } from "./TaskSchedulePicker";
import { Btn, ErrorLine, inputStyle } from "./roomLinkUi";

export const TEXT_MAX = 2000;
export const MAX_PEOPLE = 20;

/** onCreated 的 notice: 有人没发出去的原因 —— 弹层马上要关, 交给进度页接着显示。 */
export default function TaskComposer({ onClose, onCreated }: {
  onClose: () => void;
  onCreated: (taskId: string, notice?: string) => void;
}) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [deadline, setDeadline] = useState("");
  const [recipients, setRecipients] = useState<string[]>([]);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const onPickError = useCallback((m: string) => setError(m), []);
  const [when, setWhen] = useState<ScheduleValue>({ schedule: null, problem: null });
  const onWhen = useCallback((v: ScheduleValue) => setWhen(v), []);
  const periodic = when.schedule?.kind === "cron";

  const all = allRecipients(recipients, typed);
  const canSend = !busy && text.trim().length > 0 && text.length <= TEXT_MAX && all.length > 0 && all.length <= MAX_PEOPLE
    && !when.problem;

  async function send() {
    setBusy(true);
    setError(null);
    try {
      const { task } = await taskCreate({ title, text, deadline: periodic ? null : deadline || null,
                                          schedule: when.schedule, recipients: all });
      let notice: string | undefined;
      try {
        const r = await taskSend(task.task_id);
        if (r.failed.length) {
          notice = `${r.failed.length} 人没发出去 (可以在这里再发): ${r.failed.map((f) => `${f.to} — ${f.error}`).join("; ")}`;
        }
      } catch (e) {
        notice = `任务存好了, 但没发出去: ${e instanceof Error ? e.message : String(e)}`;
      }
      void refreshTasks();
      onCreated(task.task_id, notice);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Overlay title="请同事帮忙" onClose={onClose} width={680}>
      <span style={muted}>
        每位同事各自决定接不接; 接下后在他自己的电脑上由他的小鲶 (或他本人) 做, 用到的工具、要发出去的东西都由他本人放行,
        核对后交回。结果只回到你这台电脑, 中央只转交加密内容、看不到原文, 取走即清空。
      </span>
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} maxLength={TEXT_MAX}
                placeholder="想请同事做什么, 如「把本周客户拜访情况整理成三行发我, 周例会用」—— 对方接不接之前看到的就是这段话"
                style={{ ...inputStyle, resize: "vertical" }} />
      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 8 }}>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="简短的名字 (可不填, 默认取第一行)" style={inputStyle} />
        {periodic ? <span /> : (
          <input type="datetime-local" value={deadline} onChange={(e) => setDeadline(e.target.value)} title="截止时间 (可不填)" style={inputStyle} />
        )}
      </div>
      <span style={muted}>{text.length}/{TEXT_MAX}{periodic || deadline ? "" : " · 不填截止就是不限时"}</span>

      <strong style={{ fontSize: 13 }}>什么时候做</strong>
      <TaskSchedulePicker onChange={onWhen} />
      {when.problem && <span style={{ ...muted, color: "var(--status-warn, #d97706)" }}>{when.problem}</span>}

      <strong style={{ fontSize: 13 }}>请谁帮忙 (最多 {MAX_PEOPLE} 人)</strong>
      <RecipientPicker picked={recipients} onPicked={setRecipients} typed={typed} onTyped={setTyped} onError={onPickError} />
      {all.length > MAX_PEOPLE && <ErrorLine>一次最多 {MAX_PEOPLE} 人, 现在 {all.length} 人</ErrorLine>}

      {error && <ErrorLine>{error}</ErrorLine>}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <Btn kind="ghost" onClick={onClose}>取消</Btn>
        <Btn kind="primary" disabled={!canSend} onClick={send}>{busy ? "发送中…" : `发给 ${all.length || ""} 位同事`}</Btn>
      </div>
    </Overlay>
  );
}
