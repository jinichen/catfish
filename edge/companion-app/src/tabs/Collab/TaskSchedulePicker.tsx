/**
 * 派任务时「什么时候做」(11/4, docs/COLLEAGUE-TASK-PLAN.md §2): 马上 / 定时做一次 / 周期。
 * 周期的写法跟工作流定时同一套 (WorkflowParts.toCron / cronText); 周期必须定结束 (到哪天, 或一共几次)。
 */
import { useEffect, useState } from "react";

import type { Schedule } from "../../lib/tauri_colleague_tasks";
import { WEEKDAYS, cronText, toCron, type Freq } from "../Dashboard/WorkflowParts";
import { muted } from "./formsUi";
import { inputStyle } from "./roomLinkUi";

type Mode = "now" | "once" | "cron";

/** 交给父组件的: schedule (马上 = null) 和还不能发的原因 (null = 可以发)。 */
export interface ScheduleValue {
  schedule: Schedule | null;
  problem: string | null;
}

export function scheduleText(s: Schedule | null | undefined): string {
  if (!s) return "马上做";
  if (s.kind === "once") return `${s.at.replace("T", " ")} 做一次`;
  const end = [s.until ? `到 ${s.until} 为止` : "", s.times ? `最多 ${s.times} 次` : ""].filter(Boolean).join("、");
  return `${cronText(s.expr)}, ${end}`;
}

export default function TaskSchedulePicker({ onChange, initial }: {
  onChange: (v: ScheduleValue) => void;
  initial?: Schedule | null;
}) {
  const [mode, setMode] = useState<Mode>(initial ? initial.kind : "now");
  const [at, setAt] = useState(initial?.kind === "once" ? initial.at : "");
  const [freq, setFreq] = useState<Freq>("weekly");
  const [time, setTime] = useState("09:00");
  const [weekday, setWeekday] = useState(1);
  const [monthday, setMonthday] = useState(1);
  const [until, setUntil] = useState(initial?.kind === "cron" ? initial.until ?? "" : "");
  const [times, setTimes] = useState(initial?.kind === "cron" && initial.times ? String(initial.times) : "");
  const [rawExpr] = useState(initial?.kind === "cron" ? initial.expr : null);   // 改任务时保留原写法, 动了频率才换

  const [touched, setTouched] = useState(false);
  const expr = !touched && rawExpr ? rawExpr : toCron(freq, time, weekday, monthday);

  useEffect(() => {
    if (mode === "now") return onChange({ schedule: null, problem: null });
    if (mode === "once") {
      if (!at) return onChange({ schedule: null, problem: "选一下哪天几点做" });
      return onChange({ schedule: { kind: "once", at }, problem: null });
    }
    const n = times.trim() ? Number.parseInt(times, 10) : null;
    if (!until && !n) return onChange({ schedule: null, problem: "周期任务要定结束: 到哪天为止, 或一共几次" });
    if (n !== null && (!Number.isFinite(n) || n < 1 || n > 100)) return onChange({ schedule: null, problem: "次数要在 1 到 100 之间" });
    onChange({ schedule: { kind: "cron", expr, until: until || null, times: n }, problem: null });
  }, [mode, at, expr, until, times, onChange]);

  const sel = { ...inputStyle, width: "auto", padding: "6px 8px" };
  const touch = <T,>(set: (v: T) => void) => (v: T) => { setTouched(true); set(v); };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", gap: 12, fontSize: 13 }}>
        {([["now", "马上做"], ["once", "定时做一次"], ["cron", "周期做"]] as const).map(([m, label]) => (
          <label key={m} style={{ display: "flex", gap: 4, alignItems: "center" }}>
            <input type="radio" name="task-when" checked={mode === m} onChange={() => setMode(m)} />{label}
          </label>
        ))}
      </div>
      {mode === "once" && (
        <input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} style={sel} aria-label="哪天几点做" />
      )}
      {mode === "cron" && (
        <>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 13 }}>
            <select value={freq} onChange={(e) => touch(setFreq)(e.target.value as Freq)} style={sel} aria-label="频率">
              <option value="daily">每天</option>
              <option value="weekdays">工作日</option>
              <option value="weekly">每周</option>
              <option value="monthly">每月</option>
            </select>
            {freq === "weekly" && (
              <select value={weekday} onChange={(e) => touch(setWeekday)(Number(e.target.value))} style={sel} aria-label="周几">
                {WEEKDAYS.map((w, i) => <option key={w} value={i}>{w}</option>)}
              </select>
            )}
            {freq === "monthly" && (
              <select value={monthday} onChange={(e) => touch(setMonthday)(Number(e.target.value))} style={sel} aria-label="几号">
                {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => <option key={d} value={d}>{d} 号</option>)}
              </select>
            )}
            <input type="time" value={time} onChange={(e) => touch(setTime)(e.target.value)} style={sel} aria-label="几点" />
            <span style={muted}>= {cronText(expr)}</span>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 13 }}>
            <span>结束:</span>
            <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} style={sel} aria-label="到哪天为止" />
            <span>和/或 一共</span>
            <input type="number" min={1} max={100} value={times} onChange={(e) => setTimes(e.target.value)}
                   style={{ ...sel, width: 70 }} aria-label="一共几次" />
            <span>次</span>
          </div>
        </>
      )}
      {mode !== "now" && (
        <span style={muted}>
          到点在同事自己的电脑上由他的小鲶做 (要开着电脑、鲶鱼在运行; 错过了开机补做)。没人在旁边时要审批的步骤不会执行,
          小鲶会在结果里写明; 同事默认每次核对后再交回。
        </span>
      )}
    </div>
  );
}
