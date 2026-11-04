/**
 * 同事派给我的定时 / 周期任务 (11/4, docs/COLLEAGUE-TASK-PLAN.md §2)。
 *
 * 接下 = 在我电脑上建定时任务, 到点小鲶做; 可以勾「每次做完直接交回」(默认每次我核对后交回)。
 * 每一期一个框: 待核对的改好点「交回」; 交回过的可以改了再交。
 * 错过了 / 定时没了 / 想提前做: 「现在让小鲶做一次」(有人在, 对外动作照常问)。不想做了: 「退出」并写原因。
 */
import { useState } from "react";

import { doChatKey, doNowPrompt } from "../../lib/taskPrompts";
import {
  taskAccept, taskDecline, taskPeriodSubmit, taskQuit,
  type IncomingState, type IncomingTask, type MyPeriod,
} from "../../lib/tauri_colleague_tasks";
import { useUIStore } from "../../store/ui";
import { StateTag, muted } from "./formsUi";
import { Btn, inputStyle } from "./roomLinkUi";

const when = (iso?: string | null) => (iso ? iso.slice(0, 16).replace("T", " ") : "");

function PeriodBox({ p, busy, onSubmit }: { p: MyPeriod; busy: boolean; onSubmit: (text: string) => void }) {
  const [text, setText] = useState(p.result);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 500 }}>
          第 {p.period} 次 · {when(p.at)}{p.late_minutes ? ` · 晚了 ${Math.round(p.late_minutes / 60) || 1} 小时左右` : ""}
        </span>
        <StateTag state={p.state === "待核对" ? "待核对" : "已交回"} />
      </div>
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={5} style={{ ...inputStyle, resize: "vertical" }}
                aria-label={`第 ${p.period} 次的结果`} />
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <Btn kind={p.state === "待核对" ? "primary" : "ghost"} disabled={busy || !text.trim()} onClick={() => onSubmit(text)}>
          {p.state === "待核对" ? "交回" : "改了再交"}
        </Btn>
      </div>
    </div>
  );
}

export default function TaskWorkScheduled({ rec, state, scheduleText, nextRun, busy, run, onClose }: {
  rec: IncomingTask;
  state: IncomingState | "";
  scheduleText: string;
  nextRun: string | null | undefined;
  busy: boolean;
  run: (fn: () => Promise<string | void>) => void;
  onClose: () => void;
}) {
  const [auto, setAuto] = useState(!!rec.auto_return);
  const [leaving, setLeaving] = useState<"decline" | "quit" | null>(null);
  const [reason, setReason] = useState("");
  const startChat = useUIStore((s) => s.startEmailChat);
  const stopped = state === "已取消" || state === "已退出" || state === "不接";
  const needAccept = state === "待处理" || state === "要重新同意";
  const periods = [...(rec.periods ?? [])].reverse();

  return (
    <>
      <div style={{ fontSize: 13 }}>⏰ {scheduleText}{nextRun && !stopped && !needAccept ? ` · 下次 ${when(nextRun)}` : ""}</div>
      {state === "要重新同意" && <div style={{ ...muted, color: "var(--status-warn, #d97706)" }}>对方改了任务, 定时先停了, 看过再决定接不接。</div>}
      {state === "没做成" && <div style={{ ...muted, color: "var(--status-warn, #d97706)" }}>到点电脑没开 (补跑也没成), 可以现在让小鲶做一次。</div>}
      {state === "定时没了" && <div style={{ ...muted, color: "var(--status-warn, #d97706)" }}>这个任务的定时被删了。要继续就点「重新安排」, 不做了就退出。</div>}
      {rec.quit && <div style={muted}>你已退出: {rec.quit.reason}</div>}
      {rec.declined && <div style={muted}>你回了不接: {rec.declined.reason}</div>}

      {needAccept && !leaving && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label style={{ fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}>
            <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
            每次做完直接交回, 不用我看 (不勾 = 每次我核对后再交回)
          </label>
          <div style={{ display: "flex", gap: 8 }}>
            <Btn kind="primary" disabled={busy} onClick={() => run(async () => {
              const r = await taskAccept(rec.task_id, auto);
              return `接下了, 到点小鲶会做${r.next_run ? ` (下次 ${when(r.next_run)})` : ""}`;
            })}>🐟 接下, 到点让小鲶做</Btn>
            <Btn kind="ghost" disabled={busy} onClick={() => setLeaving("decline")}>不接</Btn>
          </div>
          <span style={muted}>到点要开着电脑、鲶鱼在运行; 错过了开机补做。没人在旁边时要审批的步骤不会执行, 小鲶会在结果里写明。</span>
        </div>
      )}

      {!stopped && !needAccept && !leaving && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Btn kind="ghost" disabled={busy} onClick={() => { startChat(doChatKey(rec.task_id), doNowPrompt(rec)); onClose(); }}>
            🐟 现在让小鲶做一次
          </Btn>
          {state === "定时没了" && (
            <Btn kind="ghost" disabled={busy} onClick={() => run(async () => { await taskAccept(rec.task_id, !!rec.auto_return); return "重新安排好了"; })}>
              重新安排
            </Btn>
          )}
          {state !== "已结束" && <Btn kind="ghost" disabled={busy} onClick={() => setLeaving("quit")}>退出</Btn>}
          <span style={muted}>{rec.auto_return ? "每次做完直接交回" : "每次做完等你核对后交回"}</span>
        </div>
      )}

      {leaving && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <input value={reason} onChange={(e) => setReason(e.target.value)} style={inputStyle}
                 placeholder={leaving === "quit" ? "说一句为什么退出 (会发给对方)" : "说一句为什么不接 (会发给对方)"} />
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <Btn kind="ghost" onClick={() => setLeaving(null)}>算了</Btn>
            <Btn kind="primary" disabled={busy || !reason.trim()} onClick={() => run(async () => {
              if (leaving === "quit") await taskQuit(rec.task_id, reason);
              else await taskDecline(rec.task_id, reason);
              setLeaving(null);
              return leaving === "quit" ? "已退出, 本机定时删掉了" : "已告诉对方你不接";
            })}>告诉对方</Btn>
          </div>
        </div>
      )}

      {periods.length > 0 && <strong style={{ fontSize: 13 }}>每次的结果</strong>}
      {periods.map((p) => (
        <PeriodBox key={p.period} p={p} busy={busy} onSubmit={(text) => run(async () => {
          await taskPeriodSubmit(rec.task_id, p.period, text);
          return `第 ${p.period} 次交回了`;
        })} />
      ))}
      {periods.length === 0 && !needAccept && !stopped && <span style={muted}>还没到点, 做完的结果会出现在这里。</span>}
    </>
  );
}
