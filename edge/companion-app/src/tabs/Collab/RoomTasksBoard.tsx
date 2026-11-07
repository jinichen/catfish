/**
 * 群里的「任务」面板 (11/07, docs/GROUP-CHAT-PLAN.md §4.10)。
 *
 * 鸿波: 任务卡散在聊天记录里, 聊多了就翻不到, 要一个个去问「做了没」。
 * 群名旁边「📋 任务 3」, 点开是这个群派过的任务: 一个任务一行, 每人一格 (还没回 / 在做 / 已交回 / 不接),
 * 过了截止还没交的标红、排最前。点标题跳到聊天里那张任务卡; 我派的能「催一下」「看结果」, 派给我的能「去处理」。
 * 数据是本机群记录里的任务卡 + 状态 + 延期 (colleague_rooms_tasks.room_tasks), 别人的结果不在这里 —— 结果只回发起人。
 */
import { useState } from "react";

import { taskRemind } from "../../lib/tauri_colleague_tasks";
import type { RoomMember, RoomTaskPerson, RoomTaskRow } from "../../lib/tauri_colleague_rooms";
import { muted } from "./formsUi";
import { Btn, ErrorLine } from "./roomLinkUi";
import { STATE_WORD } from "./roomTasks";

export interface RoomTasksData { tasks: RoomTaskRow[]; open: number; late: number }

const OPEN = new Set<RoomTaskPerson["state"]>(["waiting", "accepted"]);

function nameOf(members: RoomMember[], email: string, me: string): string {
  if (email === me) return "我";
  const m = members.find((x) => x.email === email);
  return m?.name || email.split("@")[0];
}

/** 「11/9 18:00」; 只有日期就「11/9」 */
export function shortDeadline(iso: string | null | undefined): string {
  const m = (iso ?? "").match(/^\d{4}-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/);
  if (!m) return "";
  return `${+m[1]}/${+m[2]}${m[3] ? ` ${m[3]}:${m[4]}` : ""}`;
}

/** 按钮上的数: 进行中的任务数; 有逾期的单独说 */
export function tasksButtonLabel(d: RoomTasksData | null): string {
  if (!d || !d.tasks.length) return "📋 任务";
  return `📋 任务 ${d.open}${d.late ? ` · ${d.late} 逾期` : ""}`;
}

export function personColor(p: RoomTaskPerson, late: boolean): string | undefined {
  if (p.state === "done") return "var(--status-ok, #059669)";
  if (OPEN.has(p.state) && late) return "var(--status-error, #dc2626)";
  if (p.state === "declined" || p.state === "quit") return "var(--catfish-text-muted)";
  return undefined;
}

export default function RoomTasksBoard({ data, me, members, onJump, onWatch, onWork, onClose, onChanged }: {
  data: RoomTasksData | null; me: string; members: RoomMember[];
  onJump: (seq: number) => void; onWatch: (taskId: string) => void; onWork: (taskId: string) => void;
  onClose: () => void; onChanged: () => void;
}) {
  const [all, setAll] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const tasks = data?.tasks ?? [];
  const rows = all ? tasks : tasks.filter((t) => t.open);
  const link = { border: "none", background: "transparent", cursor: "pointer", fontSize: 12, padding: 0, color: "var(--catfish-cyan)" } as const;

  async function remind(t: RoomTaskRow) {
    setBusy(t.task_id);
    setErr(null);
    setInfo(null);
    try {
      const r = await taskRemind(t.task_id);
      setInfo(r.reminded.length ? `「${t.title}」催了 ${r.reminded.length} 人` : (r as { note?: string }).note ?? "都交回了, 不用催");
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div style={{ border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-sm)", padding: 8, display: "flex",
                  flexDirection: "column", gap: 6, flexShrink: 0 }} aria-label="群里的任务">
      <div style={{ display: "flex", gap: 8, alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}>
        <strong style={{ fontSize: 13 }}>
          这个群派过的任务{data ? ` · 进行中 ${data.open}` : ""}
          {data?.late ? <span style={{ color: "var(--status-error, #dc2626)" }}> · {data.late} 个有人逾期</span> : null}
        </strong>
        <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {tasks.length > rows.length || all ? (
            <button type="button" style={link} onClick={() => setAll((v) => !v)}>{all ? "只看进行中" : `也看已结束的 (${tasks.length - rows.length})`}</button>
          ) : null}
          <Btn kind="ghost" onClick={onClose}>收起</Btn>
        </span>
      </div>
      {err && <ErrorLine>{err}</ErrorLine>}
      {info && <span style={{ fontSize: 12, color: "var(--status-ok, #16a34a)" }}>{info}</span>}
      {!data && <span style={muted}>读取中…</span>}
      {data && !rows.length && (
        <span style={muted}>{tasks.length ? "进行中的都做完了。" : "这个群还没派过任务。在输入框里 @ 人说要做什么, 再点「派给@的人」。"}</span>
      )}
      {rows.length > 0 && (
        <div style={{ maxHeight: 260, overflowY: "auto", display: "flex", flexDirection: "column", gap: 6 }}>
          {rows.map((t) => {
            const late = t.late > 0;
            const mine = t.owner === me;
            const myOpen = t.people.some((p) => p.email === me && (OPEN.has(p.state) || (t.schedule && p.state !== "quit")));
            return (
              <div key={t.task_id} style={{ display: "flex", flexDirection: "column", gap: 2, padding: "4px 6px", borderRadius: 4,
                                            borderLeft: `3px solid ${late ? "var(--status-error, #dc2626)" : t.open ? "var(--catfish-cyan)" : "var(--catfish-border)"}` }}>
                <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap", fontSize: 13 }}>
                  <button type="button" onClick={() => t.seq && onJump(t.seq)} title="跳到聊天里这张任务卡"
                          style={{ ...link, fontSize: 13, color: "inherit", fontWeight: 600, textAlign: "left", maxWidth: "100%",
                                   overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {t.title}
                  </button>
                  <span style={{ ...muted, color: late ? "var(--status-error, #dc2626)" : undefined }}>
                    {t.schedule ? `⏰ ${t.schedule}` : t.deadline ? `截止 ${shortDeadline(t.deadline)}${late ? ` · ${t.late} 人逾期` : ""}` : "不限时"}
                  </span>
                  <span style={muted}>{mine ? "我派的" : `${nameOf(members, t.owner, me)} 派的`}</span>
                  <span style={{ display: "flex", gap: 10, marginLeft: "auto" }}>
                    {mine && t.open && <button type="button" style={link} disabled={busy === t.task_id} onClick={() => void remind(t)}>
                      {busy === t.task_id ? "催…" : "催一下"}</button>}
                    {mine && <button type="button" style={link} onClick={() => onWatch(t.task_id)}>看结果</button>}
                    {myOpen && <button type="button" style={link} onClick={() => onWork(t.task_id)}>去处理</button>}
                  </span>
                </div>
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap", fontSize: 12 }}>
                  {t.people.map((p) => (
                    <span key={p.email} title={p.email} style={{ color: personColor(p, late) }}>
                      {nameOf(members, p.email, me)} {STATE_WORD[p.state]}{p.period ? ` 第 ${p.period} 期` : ""}
                    </span>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
