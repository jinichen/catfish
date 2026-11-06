/**
 * 「协同」页 → 群聊 (11/5, docs/GROUP-CHAT-PLAN.md 第一步)。
 *
 * 左: 群列表 (等我加入的排最前, 未读 / @我 红点); 中: 聊天; 右: 成员 + 群主管理 (拉人 / 移出 / 改名和公告 / 解散) / 退群。
 * 一条消息 = 给每个已加入的成员各投一封, 聊天记录只在各自电脑上; 每一封都是员工自己点了才发。
 * 正打开某个群时 3 秒取一次邮筒 (colleagueRoomsStore.setActiveRoom), 平时 20 秒。
 * 第二步 (同日): 在聊天里直接派任务 —— 照常打字 @ 人, 「周五前」「每周五下午5点, 做4次」「先让@张三…再让@李四…」
 *   输入框下面实时显示认出来的 (派给谁 / 截止 / 定时 / 周期 / 分几段), 点「派给@的人」(或 Ctrl/⌘+Enter) 才派。
 *   群里显示的就是这句话, 下面一行每人的状态; 被派到的人在这条消息上点「接下」「交回」。结果只回发起人。
 * 第三步 (同日): 「🐟 小鲶」—— 总结讨论 / 整理待办 / 起草回复, 读本机群记录出草稿填进输入框, 自己改了再发;
 *   派定时 / 周期任务时可以勾「每次结果自动发到群里」(唯一的自动发群特例, 发起人电脑收到结果时转进群)。
 */
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { refreshRooms, setActiveRoom, useColleagueRooms } from "../../lib/colleagueRoomsStore";
import { refreshTasks, useColleagueTasks } from "../../lib/colleagueTasksStore";
import { pipelineCreate, taskAccept, taskCreate, taskGet, taskSend } from "../../lib/tauri_colleague_tasks";
import {
  failedNotice, roomAssist, roomDecline, roomDelete, roomDisband, roomGet, roomJoin, roomLeave, roomRead, roomRemove, roomSend, roomUpdate,
  type AssistKind,
  type RoomDetail, type RoomMember, type RoomMessage, type RoomSummary,
} from "../../lib/tauri_colleague_rooms";
import { Overlay, muted } from "./formsUi";
import RoomComposer, { NAME_MAX } from "./RoomComposer";
import { STATE_WORD, cardStates, parseChatTask, summaryText, type ChatTask, type SingleTask } from "./roomTasks";
import TaskProgress from "./TaskProgress";
import TaskWork from "./TaskWork";
import { Btn, ErrorLine, cardStyle, inputStyle } from "./roomLinkUi";

export const TEXT_MAX = 4000;

const STATE_LABEL: Record<string, string> = {
  invited: "已邀请", joined: "已加入", declined: "没加入", left: "已退出", removed: "被移出",
};
const ENDED_LABEL: Record<string, string> = {
  declined: "你没加入这个群", left: "你已退出这个群", removed: "你已被群主移出这个群", disbanded: "群主已解散这个群",
};

export function labelOf(members: RoomMember[], email: string | undefined): string {
  if (!email) return "";
  const m = members.find((x) => x.email === email);
  return m?.name || email.split("@")[0];
}

/** 消息里写了「@名字」的成员 (不含自己、不含已离开的)。 */
export function mentionsIn(text: string, members: RoomMember[], me: string): string[] {
  return members
    .filter((m) => m.email !== me && (m.state === "joined" || m.state === "invited"))
    .filter((m) => text.includes(`@${m.name || m.email.split("@")[0]}`))
    .map((m) => m.email);
}

/** 被邀请了、还没点「加入」的人 (不含自己): 群消息不发给他们。 */
export function notJoinedYet(members: RoomMember[], me: string): RoomMember[] {
  return members.filter((m) => m.state === "invited" && m.email !== me);
}

export function waitingNotice(waiting: RoomMember[]): string {
  const names = waiting.map((m) => m.name || m.email.split("@")[0]);
  const shown = names.length > 3 ? `${names.slice(0, 3).join("、")} 等 ${names.length} 人` : names.join("、");
  return `${shown} 还没点「加入」, 现在发的消息他们收不到 (加入后也看不到之前的)。`;
}

/** 群列表里最后一条的预览: 任务状态 / 自动转发的文字里已经有名字了 */
export function lastPreview(last: { name: string; text: string; kind?: string }): string {
  return last.kind === "status" || last.kind === "auto" ? last.text : `${last.name}: ${last.text}`;
}

/** 截止 / 时间写短: 「2026-11-06」→「11/6」,「2026-11-06T15:00」→「11/6 15:00」(今年以外带年份) */
export function shortWhen(iso: string | null | undefined, now: Date = new Date()): string {
  const m = (iso ?? "").match(/^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/);
  if (!m) return iso ?? "";
  const day = `${+m[1] !== now.getFullYear() ? `${m[1]}/` : ""}${+m[2]}/${+m[3]}`;
  return m[4] ? `${day} ${m[4]}:${m[5]}` : day;
}

/** 状态行要不要单独显示: 那条任务就在眼前时, 状态已经写在它下面那一行了, 不再单独一行 (11/5 鸿波: 太乱) */
export function visibleMessages(messages: RoomMessage[]): RoomMessage[] {
  const cards = new Set(messages.filter((m) => m.kind === "card" && m.card).map((m) => m.card!.task_id));
  return messages.filter((m) => !(m.kind === "status" && m.status && cards.has(m.status.task_id)));
}

export function timeLabel(at: string, now: Date = new Date()): string {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return "";
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return d.toDateString() === now.toDateString() ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
}

/** 系统消息 / 状态: 一行, 放不下省略 (原来居中折成两行, 时间单独掉到下一行) */
const oneLine = {
  fontSize: 12, color: "var(--catfish-text-muted)", textAlign: "center" as const, whiteSpace: "nowrap" as const,
  overflow: "hidden", textOverflow: "ellipsis", maxWidth: "100%", padding: "0 12px",
};

const listButton = (on: boolean) => ({
  textAlign: "left" as const, cursor: "pointer", font: "inherit", color: "inherit", width: "100%",
  border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-sm, 6px)", padding: "8px 10px",
  background: on ? "var(--catfish-bg-subtle, rgba(0,0,0,0.05))" : "transparent",
  display: "flex", flexDirection: "column" as const, gap: 2,
});

function Badge({ n, at }: { n: number; at?: boolean }) {
  if (!n) return null;
  return (
    <span style={{ fontSize: 10, minWidth: 16, padding: "0 5px", borderRadius: 8, textAlign: "center",
                   background: at ? "var(--status-warn, #d97706)" : "var(--status-error, #dc2626)", color: "white" }}>
      {at ? "@" : ""}{n > 99 ? "99+" : n}
    </span>
  );
}

export default function RoomsPanel() {
  const { rooms, loaded, syncError } = useColleagueRooms();
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState<string | undefined>();

  // 列表里第一个群默认打开 (有等我加入的就是它)
  useEffect(() => {
    if (!open && rooms.length) setOpen(rooms[0].room_id);
    if (open && loaded && !rooms.some((r) => r.room_id === open)) setOpen(rooms[0]?.room_id ?? null);
  }, [rooms, open, loaded]);
  useEffect(() => {
    setActiveRoom(open);
    return () => setActiveRoom(null);
  }, [open]);

  return (
    <div style={{ ...cardStyle, flex: 1, minHeight: 0, height: "auto", overflow: "hidden", display: "flex", flexDirection: "column", gap: 8 }}>
      {syncError && <span style={muted}>暂时收不到群消息: {syncError}</span>}

      <div style={{ display: "grid", gridTemplateColumns: "200px minmax(0, 1fr)", gridTemplateRows: "minmax(0, 1fr)",
                    gap: "var(--space-3)", flex: 1, minHeight: 0 }}>
        {/* 左: 群列表 (自己滚) */}
        <div style={{ display: "flex", flexDirection: "column", gap: 6, minHeight: 0 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexShrink: 0 }}>
            <strong style={{ fontSize: 13 }} title="消息只存在每个成员自己的电脑上, 中央只转交加密内容、看不到原文, 取走即清空, 不保存记录; 每一条都是你点了才发">
              💬 群聊
            </strong>
            <Btn kind="primary" onClick={() => setCreating(true)}>建群</Btn>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6, overflowY: "auto", minHeight: 0, flex: 1 }}>
            {loaded && rooms.length === 0 && <span style={muted}>还没有群。点「建群」, 拉几位同事进来。</span>}
            {rooms.map((r) => <RoomRow key={r.room_id} r={r} on={r.room_id === open} onClick={() => { setNotice(undefined); setOpen(r.room_id); }} />)}
          </div>
        </div>
        {open ? <RoomView key={open} roomId={open} notice={notice} onNotice={setNotice} /> : (
          <div style={{ ...muted, alignSelf: "center", justifySelf: "center" }}>选一个群, 或者建一个新群。</div>
        )}
      </div>

      {creating && <RoomComposer onClose={() => setCreating(false)}
                                 onDone={(id, n) => { setCreating(false); setNotice(n); setOpen(id); }} />}
    </div>
  );
}

function RoomRow({ r, on, onClick }: { r: RoomSummary; on: boolean; onClick: () => void }) {
  const sub = r.my_state === "invited" ? "邀请你加入" : r.my_state !== "joined" ? ENDED_LABEL[r.my_state] :
    r.last ? lastPreview(r.last) : `${r.joined} 人已加入`;
  return (
    <button type="button" onClick={onClick} style={listButton(on)}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 6, alignItems: "center" }}>
        <span style={{ fontSize: "var(--text-sm)", fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {r.name}
        </span>
        <span style={{ display: "flex", gap: 4 }}>
          {r.my_state === "invited" && <Badge n={1} />}
          <Badge n={r.mentioned} at />
          <Badge n={r.unread - r.mentioned} />
        </span>
      </div>
      <span style={{ ...muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{sub}</span>
    </button>
  );
}

function RoomView({ roomId, notice, onNotice }: { roomId: string; notice?: string; onNotice: (n?: string) => void }) {
  const { version } = useColleagueRooms();
  const { incoming } = useColleagueTasks();
  const [d, setD] = useState<RoomDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState("");
  const [replyTo, setReplyTo] = useState<RoomMessage | null>(null);
  const [atOpen, setAtOpen] = useState(false);
  const [showMembers, setShowMembers] = useState(false);
  const [modal, setModal] = useState<null | "add" | "info" | "disband" | "leave" | { remove: RoomMember }>(null);
  const [work, setWork] = useState<string | null>(null);
  const [watch, setWatch] = useState<{ id: string; notice?: string } | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  /** 提示 (小鲶起草好了 / 接下了 / 派出了): 不是错误, 不用红字, 几秒后自己消失 */
  const [hint, setHint] = useState<string | null>(null);
  useEffect(() => {
    if (!hint) return;
    const t = setTimeout(() => setHint(null), 6000);
    return () => clearTimeout(t);
  }, [hint]);
  const [helping, setHelping] = useState<AssistKind | null>(null);
  const [autoPost, setAutoPost] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const r = await roomGet(roomId);
      setD(r);
      setError(null);
      if (r.room.my_state === "joined" && r.room.unread) {
        await roomRead(roomId);
        void refreshRooms();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [roomId]);
  useEffect(() => { void load(); }, [load, version]);
  const count = d?.messages.length ?? 0;
  useEffect(() => { bottom.current?.scrollIntoView?.({ block: "end" }); }, [count]);

  const act = async (fn: () => Promise<{ failed?: { email: string; error: string }[] } | unknown>, what?: string) => {
    setBusy(true);
    setError(null);
    try {
      const r = (await fn()) as { failed?: { email: string; error: string }[] };
      onNotice(r?.failed ? failedNotice(r.failed, what) : undefined);
      await load();
      void refreshRooms();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const byId = useMemo(() => new Map((d?.messages ?? []).map((m) => [m.msg_id, m])), [d]);
  if (!d) return <div style={muted}>{error ? <ErrorLine>{error}</ErrorLine> : "读取中…"}</div>;
  const { room, members, me } = d;
  const owner = room.owner === me;
  const joined = room.my_state === "joined";
  const people = members.filter((m) => m.state === "joined" || m.state === "invited");
  const waiting = notJoinedYet(members, me);
  const asTask: ChatTask | null = joined ? parseChatTask(text, members, me) : null;

  async function openWork(taskId: string) {
    await refreshTasks();   // 任务单和任务卡走两个邮筒, 先取一次, 免得任务单还没到
    setWork(taskId);
  }
  async function fillSummary(taskId: string, title: string) {
    try {
      const t = await taskGet(taskId);
      if (t.role !== "owner") return;
      setText(summaryText(title, t.progress.rows, (e) => labelOf(members, e)));
      setHint("汇总填进输入框了: 看一下、删改好再点发送。");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function askXiaonian(kind: AssistKind) {
    setHelpOpen(false);
    setHelping(kind);
    setError(null);
    try {
      // 起草回复: 输入框里写的当要求 (如「委婉拒绝」), 选了「回复」就回那一条
      const r = await roomAssist(roomId, kind, kind === "reply" ? text : "", kind === "reply" ? replyTo?.msg_id : null);
      setText(r.text);
      setHint("小鲶起草好了, 在输入框里 —— 看一下、改好再发 (它不会自己发)。");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setHelping(null);
    }
  }

  async function dispatch() {
    const t = parseChatTask(text, members, me);
    if (!t || t.problem || busy) return;
    const body = text.trim();
    const ok = await act(async () => {
      if (t.kind === "pipeline") {
        const r = await pipelineCreate({ title: t.title, room_id: roomId, room_text: body,
                                         stages: t.stages.map((st) => ({ name: st.name, text: st.text, recipients: st.recipients, deadline: st.deadline || null })) });
        return { failed: r.failed.map((f) => ({ email: f.to, error: f.error })) };
      }
      const one = async (x: SingleTask, said: string) => {
        const { task } = await taskCreate({ text: x.text, recipients: x.recipients, deadline: x.deadline || null, schedule: x.schedule,
                                            room_id: roomId, room_text: said, room_auto_post: Boolean(x.schedule) && autoPost });
        const r = await taskSend(task.task_id);
        if (r.room_notice) throw new Error(r.room_notice);
        return r.failed.map((f) => ({ email: f.to, error: f.error }));
      };
      if (t.kind === "batch") {   // 多行待办: 一行派一个, 群里一行一条
        const failed = [];
        for (const x of t.items) failed.push(...(await one(x, x.text)));
        setHint(`派出了 ${t.items.length} 条任务`);
        return { failed };
      }
      return { failed: await one(t, body) };
    }, "没收到任务");
    if (ok) { setText(""); setReplyTo(null); setAutoPost(false); void refreshTasks(); }
  }

  async function send() {
    const body = text.trim();
    if (!body) return;
    const ok = await act(() => roomSend(roomId, body, mentionsIn(body, members, me), replyTo?.msg_id), "没收到这条消息");
    if (ok) { setText(""); setReplyTo(null); }
  }
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !e.nativeEvent.isComposing) { e.preventDefault(); void dispatch(); return; }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); }
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: showMembers ? "minmax(0, 1fr) 200px" : "minmax(0, 1fr)",
                  gridTemplateRows: "minmax(0, 1fr)", gap: "var(--space-3)", minHeight: 0 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, minHeight: 0 }}>
        <div style={{ borderBottom: "1px solid var(--catfish-border)", paddingBottom: 6, flexShrink: 0,
                      display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
          <div style={{ minWidth: 0 }}>
            <strong style={{ fontSize: "var(--text-sm)" }}>{room.name}</strong>
            <span style={{ ...muted, marginLeft: 8 }}>{room.joined} 人{room.members > room.joined ? ` · ${room.members - room.joined} 人还没加入` : ""}</span>
            {room.purpose && <div style={{ ...muted, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                                  title={room.purpose}>📌 {room.purpose}</div>}
          </div>
          <Btn kind="ghost" onClick={() => setShowMembers((v) => !v)}>{showMembers ? "收起成员" : `👥 成员 ${room.joined}`}</Btn>
        </div>
        {notice && <ErrorLine>{notice}</ErrorLine>}
        {hint && <div style={{ fontSize: 12, color: "var(--catfish-cyan)", flexShrink: 0 }}>{hint}</div>}
        {error && <ErrorLine>{error}</ErrorLine>}

        {/* 只有这里滚 */}
        <div style={{ flex: 1, minHeight: 0, overflowY: "auto", display: "flex", flexDirection: "column", gap: 10, paddingRight: 4 }}>
          {d.older > 0 && <span style={{ ...muted, textAlign: "center" }}>更早的 {d.older} 条没显示</span>}
          {visibleMessages(d.messages).map((m) => (
            m.kind === "card" && m.card ? (
              <TaskMessage key={m.msg_id} m={m} me={me} members={members} messages={d.messages} busy={busy}
                           local={incoming.find((t) => t.task_id === m.card!.task_id)?.state}
                           onAccept={() => void act(async () => {
                             await refreshTasks();
                             const r = await taskAccept(m.card!.task_id);
                             if (r.start_error) onNotice(r.start_error);
                             else setHint(`接下了「${m.card!.title}」, 小鲶已经在后台开工, 做好了会通知你核对`);
                             void refreshTasks();
                             return r;
                           }, "没收到你接下的通知")}
                           onWork={() => void openWork(m.card!.task_id)} onWatch={() => setWatch({ id: m.card!.task_id })}
                           onSummary={joined ? () => void fillSummary(m.card!.task_id, m.card!.title) : undefined} />
            ) : (
              <Bubble key={m.msg_id} m={m} mine={m.from === me} members={members} me={me} quoted={m.reply_to ? byId.get(m.reply_to) : undefined}
                      onReply={joined && m.kind === "text" ? () => setReplyTo(m) : undefined} />
            )
          ))}
          <div ref={bottom} />
        </div>

        {room.my_state === "invited" && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, justifyContent: "space-between",
                        border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-sm)", padding: 10 }}>
            <span style={{ fontSize: 13 }}>{labelOf(members, room.owner)} 邀请你加入「{room.name}」。加入后才收发群消息, 加入前的记录看不到。</span>
            <span style={{ display: "flex", gap: 6 }}>
              <Btn kind="ghost" disabled={busy} onClick={() => void act(() => roomDecline(roomId))}>不加入</Btn>
              <Btn kind="primary" disabled={busy} onClick={() => void act(() => roomJoin(roomId), "没收到你加入的通知")}>加入</Btn>
            </span>
          </div>
        )}
        {!joined && room.my_state !== "invited" && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, justifyContent: "space-between" }}>
            <span style={muted}>{ENDED_LABEL[room.my_state]}, 不能再发消息。</span>
            <Btn kind="ghost" disabled={busy} onClick={() => void act(() => roomDelete(roomId))}>删除本机记录</Btn>
          </div>
        )}
        {joined && (
          <div style={{ display: "flex", flexDirection: "column", gap: 4, position: "relative", flexShrink: 0 }}>
            {waiting.length > 0 && (
              <span style={{ ...muted, color: "var(--status-warn, #d97706)" }}>
                {waitingNotice(waiting)}
              </span>
            )}
            {replyTo && (
              <div style={{ ...muted, display: "flex", justifyContent: "space-between", gap: 6 }}>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  回复 {labelOf(members, replyTo.from)}: {replyTo.text}
                </span>
                <button type="button" aria-label="不回复了" onClick={() => setReplyTo(null)}
                        style={{ border: "none", background: "transparent", cursor: "pointer" }}>✕</button>
              </div>
            )}
            {atOpen && (
              <div style={{ position: "absolute", bottom: "100%", left: 0, background: "var(--catfish-bg-elevated)", zIndex: 5,
                            border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-sm)", padding: 4,
                            display: "flex", flexDirection: "column", maxHeight: 200, overflowY: "auto", minWidth: 180,
                            color: "var(--catfish-text, inherit)" }}>
                {people.filter((p) => p.email !== me).map((p) => (
                  <button key={p.email} type="button"
                          onClick={() => { setText((t) => `${t}${t.endsWith("@") ? "" : "@"}${p.name || p.email.split("@")[0]} `); setAtOpen(false); }}
                          className="room-menu-item"
                          style={{ textAlign: "left", border: "none", background: "transparent", cursor: "pointer", padding: "4px 8px",
                                   fontSize: 13, color: "inherit", borderRadius: 4 }}>
                    @{p.name || p.email.split("@")[0]}
                  </button>
                ))}
              </div>
            )}
            <textarea value={text} rows={Math.min(8, Math.max(2, text.split("\n").length))} maxLength={TEXT_MAX} onKeyDown={onKey}
                      onChange={(e) => { setText(e.target.value); setAtOpen(e.target.value.endsWith("@")); }}
                      placeholder="说点什么 · Enter 发送 · @ 提醒某人 · 要派任务就照常说, 如「@张三 周五前交拜访记录」, 再点「派给@的人」"
                      style={{ ...inputStyle, resize: "none" }} />
            {asTask && (
              <span style={{ ...muted, color: asTask.problem ? "var(--status-warn, #d97706)" : "inherit" }}>
                {asTask.problem ? `派不了: ${asTask.problem}` : `点「派给@的人」= ${asTask.label}`}
                {asTask.kind === "task" && !asTask.problem && !(asTask.schedule && autoPost) && " · 结果只回给你, 群里只显示谁接了、谁交了"}
              </span>
            )}
            {asTask?.kind === "task" && asTask.schedule && !asTask.problem && (
              <label style={{ ...muted, display: "flex", gap: 4, alignItems: "center", cursor: "pointer" }}>
                <input type="checkbox" checked={autoPost} onChange={(e) => setAutoPost(e.target.checked)} />
                每次结果自动发到群里 (群里的人都看得到; 同事接之前会看到这条, 你随时能在任务里关掉)
              </label>
            )}
            {helpOpen && (
              <div style={{ position: "absolute", bottom: "100%", right: 0, background: "var(--catfish-bg-elevated)", zIndex: 5,
                            border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-sm)", padding: 4,
                            display: "flex", flexDirection: "column", minWidth: 240, color: "var(--catfish-text, inherit)" }}>
                {([["summary", "总结一下最近的讨论"], ["todos", "整理成待办 (谁 / 做什么 / 截止)"],
                   ["reply", replyTo ? `起草回复 ${labelOf(members, replyTo.from)} 的那条` : "帮我起草回复 (输入框里可写要求)"]] as const).map(([k, label]) => (
                  <button key={k} type="button" onClick={() => void askXiaonian(k)} className="room-menu-item"
                          style={{ textAlign: "left", border: "none", background: "transparent", cursor: "pointer", padding: "6px 8px",
                                   fontSize: 13, color: "inherit", borderRadius: 4 }}>
                    {label}
                  </button>
                ))}
                <span style={{ ...muted, padding: "4px 8px" }}>小鲶读你电脑上的群记录, 出草稿放进输入框, 发不发你定。</span>
              </div>
            )}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
              <span style={{ ...muted, whiteSpace: "nowrap" }} title="长材料请用邮件, 还不能传附件">{text.length}/{TEXT_MAX}</span>
              <span style={{ display: "flex", gap: 6, whiteSpace: "nowrap", flexShrink: 0 }}>
                <Btn kind="ghost" disabled={Boolean(helping)} onClick={() => setHelpOpen((v) => !v)}>
                  {helping ? "🐟 小鲶在写…" : "🐟 小鲶"}
                </Btn>
                <Btn kind="ghost" onClick={() => setAtOpen((v) => !v)}>@</Btn>
                {asTask && (
                  <Btn kind="ghost" disabled={busy || Boolean(asTask.problem)} onClick={() => void dispatch()}>派给@的人</Btn>
                )}
                <Btn kind="primary" disabled={busy || !text.trim()} onClick={() => void send()}>{busy ? "发送中…" : "发送"}</Btn>
              </span>
            </div>
          </div>
        )}
      </div>

      {showMembers && <div style={{ display: "flex", flexDirection: "column", gap: 6, borderLeft: "1px solid var(--catfish-border)", paddingLeft: 10,
                                   minHeight: 0, overflowY: "auto" }}>
        <strong style={{ fontSize: 13 }}>成员 ({room.joined}/{room.members})</strong>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 360, overflowY: "auto" }}>
          {members.map((m) => (
            <div key={m.email} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 4 }}>
              <span style={{ fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={m.email}>
                {m.name || m.email.split("@")[0]}{m.me ? " (我)" : ""}{m.owner ? " · 群主" : ""}
              </span>
              <span style={{ display: "flex", gap: 4, alignItems: "center" }}>
                <span style={{ ...muted, whiteSpace: "nowrap" }}>{STATE_LABEL[m.state]}</span>
                {owner && joined && !m.me && (m.state === "joined" || m.state === "invited") && (
                  <button type="button" aria-label={`移出 ${m.email}`} onClick={() => setModal({ remove: m })}
                          style={{ border: "none", background: "transparent", cursor: "pointer", fontSize: 11, color: "var(--catfish-text-muted)" }}>移出</button>
                )}
              </span>
            </div>
          ))}
        </div>
        {joined && owner && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
            <Btn kind="ghost" onClick={() => setModal("add")}>拉同事进群</Btn>
            <Btn kind="ghost" onClick={() => setModal("info")}>改群名 / 公告</Btn>
            <Btn kind="ghost" onClick={() => setModal("disband")}>解散群</Btn>
          </div>
        )}
        {joined && !owner && <div style={{ marginTop: 6 }}><Btn kind="ghost" onClick={() => setModal("leave")}>退出群</Btn></div>}
      </div>}

      {work && <TaskWork taskId={work} onClose={() => { setWork(null); void load(); }} />}
      {watch && <TaskProgress taskId={watch.id} initialError={watch.notice} onClose={() => setWatch(null)} />}
      {modal === "add" && <RoomComposer roomId={roomId} existing={room.members} onClose={() => setModal(null)}
                                        onDone={(_id, n) => { setModal(null); onNotice(n); void load(); }} />}
      {modal === "info" && <InfoEditor room={room} busy={busy} onClose={() => setModal(null)}
                                       onSave={async (n, p) => { if (await act(() => roomUpdate(roomId, n, p))) setModal(null); }} />}
      {modal === "disband" && <Confirm title="解散群" text={`解散「${room.name}」? 所有成员都会收到通知, 之后谁都不能再发消息。各人电脑上的聊天记录还在。`}
                                       ok="解散" busy={busy} onClose={() => setModal(null)}
                                       onOk={async () => { if (await act(() => roomDisband(roomId))) setModal(null); }} />}
      {modal === "leave" && <Confirm title="退出群" text={`退出「${room.name}」? 群里的人会收到通知, 之后收不到这个群的消息。`}
                                     ok="退出" busy={busy} onClose={() => setModal(null)}
                                     onOk={async () => { if (await act(() => roomLeave(roomId))) setModal(null); }} />}
      {modal && typeof modal === "object" && (
        <Confirm title="移出成员" text={`把 ${modal.remove.name || modal.remove.email} 移出「${room.name}」?`} ok="移出" busy={busy}
                 onClose={() => setModal(null)}
                 onOk={async () => { if (await act(() => roomRemove(roomId, modal.remove.email))) setModal(null); }} />
      )}
    </div>
  );
}

function Bubble({ m, mine, members, me, quoted, onReply }: {
  m: RoomMessage; mine: boolean; members: RoomMember[]; me: string; quoted?: RoomMessage; onReply?: () => void;
}) {
  if (m.kind === "system") {
    return <span style={oneLine}>{m.text} · {timeLabel(m.at)}</span>;
  }
  if (m.kind === "auto") {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: mine ? "flex-end" : "flex-start", gap: 2 }}>
        <span style={muted}>🤖 自动转发 · {mine ? "我" : labelOf(members, m.from)} 的电脑 · {timeLabel(m.at)}</span>
        <div style={{ maxWidth: "78%", padding: "6px 10px", borderRadius: 10, fontSize: 13, whiteSpace: "pre-wrap", wordBreak: "break-word",
                      border: "1px dashed var(--catfish-border)", background: "var(--catfish-bg-subtle, rgba(0,0,0,0.03))" }}>
          {m.text}
        </div>
      </div>
    );
  }
  if (m.kind === "status") {
    // 只有那条任务不在眼前 (更早、没显示) 时才单独一行
    const icon = m.status?.state === "done" ? "✅" : m.status?.state === "accepted" ? "🛠" : "↩️";
    return <span style={oneLine} title={m.text}>{icon} {m.text} · {timeLabel(m.at)}</span>;
  }
  const atMe = !mine && (m.mentions ?? []).includes(me);
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: mine ? "flex-end" : "flex-start", gap: 2 }}>
      <span style={muted}>
        {mine ? "我" : labelOf(members, m.from)} · {timeLabel(m.at)}
        {m.plain && <span title="对方还是老版本, 这条没加密: 投递途中中央看得到原文" style={{ marginLeft: 6, color: "var(--status-warn, #d97706)" }}>未加密</span>}
      </span>
      <div style={{ maxWidth: "78%", padding: "6px 10px", borderRadius: 10, fontSize: 13, whiteSpace: "pre-wrap", wordBreak: "break-word",
                    background: mine ? "var(--catfish-cyan)" : "var(--catfish-bg-subtle, rgba(0,0,0,0.05))",
                    color: mine ? "white" : "inherit",
                    border: atMe ? "1px solid var(--status-warn, #d97706)" : "1px solid transparent" }}>
        {quoted && (
          <div style={{ fontSize: 11, opacity: 0.8, borderLeft: "2px solid currentColor", paddingLeft: 6, marginBottom: 4 }}>
            {labelOf(members, quoted.from)}: {quoted.text.slice(0, 80)}
          </div>
        )}
        {m.text}
      </div>
      <span style={{ display: "flex", gap: 8 }}>
        {m.undelivered?.length ? <span style={{ ...muted, color: "var(--status-warn, #d97706)" }}>{m.undelivered.length} 人没收到</span> : null}
        {onReply && (
          <button type="button" onClick={onReply}
                  style={{ border: "none", background: "transparent", cursor: "pointer", fontSize: 11, color: "var(--catfish-text-muted)", padding: 0 }}>
            回复
          </button>
        )}
      </span>
    </div>
  );
}

/** 群里派的任务: 就是发起人说的那句话, 下面一行每人的状态 + 自己能点的按钮。 */
function TaskMessage({ m, me, members, messages, busy, local, onAccept, onWork, onWatch, onSummary }: {
  m: RoomMessage; me: string; members: RoomMember[]; messages: RoomMessage[]; busy: boolean;
  /** 派给我的: 我这边这个任务现在的状态 (小鲶做好了 = 待核对) */
  local?: string;
  onAccept: () => void; onWork: () => void; onWatch: () => void; onSummary?: () => void;
}) {
  const c = m.card!;
  const mine = c.owner === me;
  const states = cardStates(c.task_id, c.assignees, messages, members);
  const myState = states.find((x) => x.email === me)?.state;
  const link = { border: "none", background: "transparent", cursor: "pointer", fontSize: 11, padding: 0, color: "var(--catfish-cyan)" };
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: mine ? "flex-end" : "flex-start", gap: 2 }}>
      {/* 11/5 鸿波: 状态别单独占一行 —— 跟「谁 · 几点」放同一行 */}
      <span style={{ ...muted, display: "flex", flexWrap: "wrap", gap: 8, justifyContent: mine ? "flex-end" : "flex-start", maxWidth: "90%" }}>
        <span>{mine ? "我" : labelOf(members, m.from)} · {timeLabel(m.at)}</span>
        <span>📋 {c.schedule ? `⏰ ${c.schedule}` : c.deadline ? `截止 ${shortWhen(c.deadline)}` : "不限时"}{c.auto_post ? " · 结果自动发群" : ""}</span>
        {states.map((x) => (
          <span key={x.email} title={x.email}
                style={{ color: x.state === "done" ? "var(--status-ok, #059669)" : undefined }}>
            {x.email === me ? "我" : x.name} {x.email === me && local === "待核对" ? "待核对 (小鲶做好了)" : STATE_WORD[x.state]}
            {x.period ? ` 第 ${x.period} 期` : ""}
          </span>
        ))}
        {myState && !c.schedule && myState === "waiting" && <button type="button" style={link} disabled={busy} onClick={onAccept}>接下</button>}
        {myState && (c.schedule || myState !== "waiting") && myState !== "done" && myState !== "declined" && myState !== "quit" && (
          <button type="button" style={link} onClick={onWork}>{c.schedule ? "去处理" : local === "待核对" ? "看一下, 交回" : "交回"}</button>
        )}
        {myState === "waiting" && !c.schedule && <button type="button" style={link} onClick={onWork}>不接</button>}
        {mine && <button type="button" style={link} onClick={onWatch}>看结果</button>}
        {mine && onSummary && <button type="button" style={link} onClick={onSummary}>汇总发到群里</button>}
      </span>
      <div style={{ maxWidth: "78%", padding: "6px 10px", borderRadius: 10, fontSize: 13, whiteSpace: "pre-wrap", wordBreak: "break-word",
                    background: mine ? "var(--catfish-cyan)" : "var(--catfish-bg-subtle, rgba(0,0,0,0.05))", color: mine ? "white" : "inherit",
                    border: myState && !["done", "declined", "quit"].includes(myState) ? "1px solid var(--status-warn, #d97706)" : "1px solid transparent",
                    opacity: myState === "done" || myState === "declined" ? 0.75 : 1 }}>
        {m.text}
      </div>
    </div>
  );
}

function InfoEditor({ room, busy, onClose, onSave }: {
  room: RoomSummary; busy: boolean; onClose: () => void; onSave: (name: string, purpose: string) => void;
}) {
  const [name, setName] = useState(room.name);
  const [purpose, setPurpose] = useState(room.purpose);
  return (
    <Overlay title="改群名 / 公告" onClose={onClose} width={520}>
      <input value={name} onChange={(e) => setName(e.target.value)} maxLength={NAME_MAX} style={inputStyle} />
      <textarea value={purpose} onChange={(e) => setPurpose(e.target.value)} rows={3} maxLength={500} placeholder="群公告 / 用途"
                style={{ ...inputStyle, resize: "vertical" }} />
      <span style={muted}>改了会通知所有成员。</span>
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <Btn kind="ghost" onClick={onClose}>取消</Btn>
        <Btn kind="primary" disabled={busy || !name.trim()} onClick={() => onSave(name.trim(), purpose.trim())}>保存</Btn>
      </div>
    </Overlay>
  );
}

function Confirm({ title, text, ok, busy, onClose, onOk }: {
  title: string; text: string; ok: string; busy: boolean; onClose: () => void; onOk: () => void;
}) {
  return (
    <Overlay title={title} onClose={onClose} width={460}>
      <span style={{ fontSize: 13 }}>{text}</span>
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <Btn kind="ghost" onClick={onClose}>取消</Btn>
        <Btn kind="primary" disabled={busy} onClick={onOk}>{busy ? "处理中…" : ok}</Btn>
      </div>
    </Overlay>
  );
}
