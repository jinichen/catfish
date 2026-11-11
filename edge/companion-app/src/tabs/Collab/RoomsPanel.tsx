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
 * 11/07 (参照 hermes 群聊): 没送到的不再弹红字, 挂在那条消息下面 (RoomsDelivery.tsx); 群主可以转让; 群里能发文件 (📎);
 *   接下就开工的任务, 我这一栏显示小鲶做到第几步。
 * 11/09 「📋 发表」: 在群里发一张表给已加入的人填 (RoomFormMessage.tsx); 表本身走填表, 群里只有表卡和谁交了。
 */
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { refreshRooms, setActiveRoom, useColleagueRooms } from "../../lib/colleagueRoomsStore";
import { refreshTasks, useColleagueTasks } from "../../lib/colleagueTasksStore";
import type { IncomingTaskSummary } from "../../lib/tauri_colleague_tasks";
import { workText } from "../../lib/taskWorkProgress";
import { pipelineCreate, taskAccept, taskCreate, taskGet, taskSend } from "../../lib/tauri_colleague_tasks";
import {
  failedNotice, roomAssist, roomDecline, roomDelete, roomDisband, roomGet, roomJoin, roomLeave, roomRead, roomRemove, roomSend, roomTasks, roomTransfer,
  roomUpdate,
  type AssistKind,
  type RoomDetail, type RoomMember, type RoomMessage, type RoomSummary,
} from "../../lib/tauri_colleague_rooms";
import { muted } from "./formsUi";
import FormComposer from "./FormComposer";
import FormFill from "./FormFill";
import FormProgress from "./FormProgress";
import RoomComposer from "./RoomComposer";
import RoomFormMessage from "./RoomFormMessage";
import RoomReviseMessage from "./RoomReviseMessage";
import ReviseMergePanel from "./ReviseMergePanel";
import DocVersions from "./DocVersions";
import { ReviseStartButton, ReviseStartDialog, hiddenReviseStatus, useReviseDetect, type ReviseSource } from "./reviseUi";
import { hiddenFormStatus } from "./roomForms";
import { Confirm, InfoEditor, RetentionDialog, SearchPanel, daysLabel } from "./RoomsExtras";
import { AttachButton, DeliveryLine, IssueBar, PickedChips, RoomFileList, pickFiles, stageFiles, type PickedFile } from "./RoomsDelivery";
import { STATE_WORD, cardStates, expectHint, latestDeadline, parseChatTask, summaryText, type ChatTask, type SingleTask } from "./roomTasks";
import RoomTasksBoard, { tasksButtonLabel, type RoomTasksData } from "./RoomTasksBoard";
import TaskProgress from "./TaskProgress";
import TaskWork from "./TaskWork";
import { Btn, ErrorLine, cardStyle, inputStyle } from "./roomLinkUi";
import { refreshForms } from "../../lib/formsStore";
import { useUIStore } from "../../store/ui";

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
  return last.kind === "status" || last.kind === "auto" || last.kind === "deadline" || last.kind === "form_status" || last.kind === "revise_status" ? last.text : `${last.name}: ${last.text}`;
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
  const forms = hiddenFormStatus(messages);
  const revs = hiddenReviseStatus(messages);   // 11/11 一起改文档: 卡片在眼前时状态写在卡片上
  return messages.filter((m) => !revs.has(m.msg_id) && !(m.kind === "status" && m.status && cards.has(m.status.task_id))
                               && !(m.kind === "form_status" && m.form_status && forms.has(m.form_status.form_id)));
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
  // 11/07: 聊天区是滚动的列; overflow:hidden 的行不写这个会被压成 0 高 (延期 / 状态行看不见)
  flexShrink: 0,
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
  const [keeping, setKeeping] = useState(false);
  const [notice, setNotice] = useState<string | undefined>();

  // 11/09 提醒条「去群里看」: 打开指定的群 (取走就清掉)
  const focus = useUIStore((s) => s.pendingRoomFocus);
  useEffect(() => {
    if (!focus || !rooms.some((r) => r.room_id === focus)) return;
    setOpen(focus);
    useUIStore.setState({ pendingRoomFocus: null });
  }, [focus, rooms]);

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
            <span style={{ display: "flex", gap: 4 }}>
              <Btn kind="ghost" onClick={() => setKeeping(true)}>记录保留</Btn>
              <Btn kind="primary" onClick={() => setCreating(true)}>建群</Btn>
            </span>
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

      {keeping && <RetentionDialog onClose={() => { setKeeping(false); void refreshRooms(); }} />}
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
  const [modal, setModal] = useState<null | "add" | "info" | "disband" | "leave" | { remove: RoomMember } | { transfer: RoomMember }>(null);
  const [picked, setPicked] = useState<PickedFile[]>([]);
  const [dragOver, setDragOver] = useState(false);
  /** 发文件时准备到百分之几 (显示在发送按钮上, 不往顶上插提示) */
  const [prep, setPrep] = useState<number | null>(null);
  const [work, setWork] = useState<string | null>(null);
  const [watch, setWatch] = useState<{ id: string; notice?: string } | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  // 11/09 群里发表: 发起弹层 / 我去填 / 发起人看进度
  const [formUi, setFormUi] = useState<null | { compose: true } | { fill: string } | { watch: string; notice?: string }>(null);
  const [reviseUi, setReviseUi] = useState<null | { merge: string; title: string } | { versions: string } | { start: ReviseSource }>(null);   // 11/11 一起改文档
  /** 提示 (小鲶起草好了 / 接下了 / 派出了): 不是错误, 不用红字, 几秒后自己消失 */
  const [hint, setHint] = useState<string | null>(null);
  useEffect(() => {
    if (!hint) return;
    const t = setTimeout(() => setHint(null), 6000);
    return () => clearTimeout(t);
  }, [hint]);
  const [helping, setHelping] = useState<AssistKind | null>(null);
  const detect = useReviseDetect(roomId, setHint);
  const [autoPost, setAutoPost] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  // 11/6 往上翻 (更早的页) / 搜索结果跳到某条前后
  const [olderPages, setOlderPages] = useState<RoomMessage[]>([]);
  const [jump, setJump] = useState<number | null>(null);
  const [searching, setSearching] = useState(false);
  // 11/07 群里的「任务」面板
  const [tasksData, setTasksData] = useState<RoomTasksData | null>(null);
  const [showTasks, setShowTasks] = useState(false);
  const [olderLeft, setOlderLeft] = useState<number | null>(null);
  const load = useCallback(async () => {
    try {
      const r = await roomGet(roomId, jump ? { around: jump } : {});
      setD(r);
      setError(null);
      roomTasks(roomId).then(setTasksData).catch(() => {});   // 面板读不出来不影响聊天
      if (r.room.my_state === "joined" && r.room.unread) {
        await roomRead(roomId);
        void refreshRooms();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [roomId, jump]);
  useEffect(() => { void load(); }, [load, version]);
  const lastSeq = d?.messages.at(-1)?.seq ?? 0;
  useEffect(() => { if (!jump) bottom.current?.scrollIntoView?.({ block: "end" }); }, [lastSeq, jump]);
  useEffect(() => {
    if (jump) document.getElementById(`room-msg-${jump}`)?.scrollIntoView?.({ block: "center" });
  }, [jump, d]);
  async function loadOlder() {
    const first = (olderPages[0] ?? d?.messages[0])?.seq;
    if (!first) return;
    try {
      const r = await roomGet(roomId, { before: first });
      setOlderPages((p) => [...r.messages, ...p]);
      setOlderLeft(r.older);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const act = async (fn: () => Promise<{ failed?: { email: string; error: string }[] } | unknown>, what?: string) => {
    setBusy(true);
    setError(null);
    try {
      const r = (await fn()) as { failed?: { email: string; error: string }[] };
      // 11/07 群里没送到的挂在那条消息下面 (自动补发 / 重发), 不再在顶上弹红字; 只有派任务 (另一个邮筒) 还在这说
      onNotice(r?.failed && what ? failedNotice(r.failed, what) : undefined);
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

  const shown = useMemo(() => {
    if (!d) return [];
    if (jump) return d.messages;
    const seen = new Set(d.messages.map((m) => m.msg_id));
    return [...olderPages.filter((m) => !seen.has(m.msg_id)), ...d.messages];
  }, [d, olderPages, jump]);
  const byId = useMemo(() => new Map(shown.map((m) => [m.msg_id, m])), [shown]);
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
        const { task } = await taskCreate({ text: x.text, expect: x.expect || null, recipients: x.recipients, deadline: x.deadline || null, schedule: x.schedule,
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
    if (!body && !picked.length) return;
    const ok = await act(async () => {
      // 大文件先一段段交给本机 (显示进度), 再发; 加密 / 传中央在后台, 消息下面看进度
      try {
        const files = picked.length ? await stageFiles(picked, (d, t) => setPrep(Math.floor((d / t) * 100))) : [];
        return await roomSend(roomId, body, mentionsIn(body, members, me), replyTo?.msg_id, files);
      } finally {
        setPrep(null);
      }
    });
    if (ok) { setText(""); setReplyTo(null); setPicked([]); }
  }
  async function addPicked(list: FileList | File[]) {
    const r = await pickFiles(list, picked);
    setPicked(r.files);
    setError(r.error ?? null);
    void detect.check(r.files.slice(picked.length));   // 带某一轮记号的交回稿: 提示直接交回
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
            {room.retention && (
              <span style={{ ...muted, marginLeft: 8 }} title="记录只存在你这台电脑上, 到期自动删掉; 取公司规定 / 群主 / 你自己设的里最短的那个">
                · 记录保留 {daysLabel(room.retention.days)} ({room.retention.label})
              </span>
            )}
            {room.purpose && <div style={{ ...muted, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                                  title={room.purpose}>📌 {room.purpose}</div>}
          </div>
          {/* 按钮不折行; 窄的时候让左边的群名 / 说明去折 */}
          <span style={{ display: "flex", gap: 8, flexShrink: 0, whiteSpace: "nowrap" }}>
            {joined && <Btn kind="ghost" onClick={() => setFormUi({ compose: true })}>📋 发表</Btn>}
            <Btn kind="ghost" onClick={() => setShowTasks((v) => !v)}>{showTasks ? "收起任务" : tasksButtonLabel(tasksData)}</Btn>
            <Btn kind="ghost" onClick={() => setSearching((v) => !v)}>🔍 搜索</Btn>
            <Btn kind="ghost" onClick={() => setShowMembers((v) => !v)}>{showMembers ? "收起成员" : `👥 成员 ${room.joined}`}</Btn>
          </span>
        </div>
        <IssueBar issue={room.issue} />
        {showTasks && <RoomTasksBoard data={tasksData} me={me} members={members} onJump={(seq) => setJump(seq)}
                                      onWatch={(id) => setWatch({ id })} onWork={(id) => void openWork(id)}
                                      onClose={() => setShowTasks(false)} onChanged={() => void load()} />}
        {searching && <SearchPanel roomId={roomId} members={members} onJump={(seq) => setJump(seq)} onClose={() => { setSearching(false); setJump(null); }} />}
        {notice && <ErrorLine>{notice}</ErrorLine>}
        {hint && <div style={{ fontSize: 12, color: "var(--catfish-cyan)", flexShrink: 0 }}>{hint}</div>}
        {error && <ErrorLine>{error}</ErrorLine>}

        {/* 只有这里滚 */}
        <div style={{ flex: 1, minHeight: 0, overflowY: "auto", display: "flex", flexDirection: "column", gap: 10, paddingRight: 4 }}>
          {jump ? (
            <span style={{ ...muted, textAlign: "center" }}>
              在看搜索结果附近的记录 · <button type="button" onClick={() => setJump(null)}
                style={{ border: "none", background: "transparent", cursor: "pointer", color: "var(--catfish-cyan)", fontSize: 12 }}>回到最新</button>
            </span>
          ) : (olderLeft ?? d.older) > 0 && (
            <button type="button" onClick={() => void loadOlder()}
                    style={{ ...muted, alignSelf: "center", border: "none", background: "transparent", cursor: "pointer", color: "var(--catfish-cyan)" }}>
              加载更早的 ({olderLeft ?? d.older} 条)
            </button>
          )}
          {visibleMessages(shown).map((m) => (<Fragment key={m.msg_id}>
            {jump !== null && m.seq === jump && (
              <span id={`room-msg-${jump}`} style={{ ...muted, color: "var(--catfish-cyan)" }}>↓ 搜到的这条</span>
            )}
            {m.kind === "card" && m.card ? (
              <TaskMessage m={m} me={me} members={members} messages={shown} busy={busy} roomId={roomId} onChanged={() => void load()}
                           local={incoming.find((t) => t.task_id === m.card!.task_id)}
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
            ) : m.kind === "revise" || m.kind === "revise_return" || m.kind === "revise_status" ? (
              <RoomReviseMessage m={m} me={me} members={members} roomId={roomId} tick={shown.length} onChanged={() => void load()} onNotice={setHint}
                                 onMerge={(id) => setReviseUi({ merge: id, title: m.revise?.title ?? "" })} onVersions={(id) => setReviseUi({ versions: id })}
                                 onRevise={(src) => setReviseUi({ start: src })} />
            ) : m.kind === "form" && m.form ? (
              <RoomFormMessage m={m} me={me} members={members} messages={shown} roomId={roomId} onChanged={() => void load()}
                               onFill={async (id) => { await refreshForms(); setFormUi({ fill: id }); }}   // 表卡和表走两个邮筒, 先取一次
                               onWatch={(id) => setFormUi({ watch: id })} />
            ) : (
              <Bubble m={m} mine={m.from === me} members={members} me={me} quoted={m.reply_to ? byId.get(m.reply_to) : undefined}
                      roomId={roomId} onChanged={() => void load()} onRevise={joined ? (src) => setReviseUi({ start: src }) : undefined}
                      onReply={joined && m.kind === "text" ? () => setReplyTo(m) : undefined} />
            )}
          </Fragment>))}
          <div ref={bottom} />
        </div>

        {room.my_state === "invited" && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, justifyContent: "space-between",
                        border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-sm)", padding: 10 }}>
            <span style={{ fontSize: 13 }}>{labelOf(members, room.owner)} 邀请你加入「{room.name}」。加入后才收发群消息, 加入前的记录看不到。</span>
            <span style={{ display: "flex", gap: 6 }}>
              <Btn kind="ghost" disabled={busy} onClick={() => void act(() => roomDecline(roomId))}>不加入</Btn>
              <Btn kind="primary" disabled={busy} onClick={() => void act(() => roomJoin(roomId))}>加入</Btn>
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
          // 11/07 文件直接拖进来 / 粘贴 (跟对话输入框一样); 不接住的话浏览器会把文件路径当文字插进输入框
          <div style={{ display: "flex", flexDirection: "column", gap: 4, position: "relative", flexShrink: 0 }}
               onDragOver={(e) => { if (e.dataTransfer?.types?.includes("Files")) { e.preventDefault(); setDragOver(true); } }}
               onDragLeave={() => setDragOver(false)}
               onDrop={(e) => {
                 if (!e.dataTransfer?.files?.length) return;
                 e.preventDefault();
                 setDragOver(false);
                 void addPicked(e.dataTransfer.files);
               }}>
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
            {detect.banner((f) => setPicked((p) => p.filter((x) => x !== f)))}
            <PickedChips files={picked} onRemove={(i) => setPicked((p) => p.filter((_, k) => k !== i))} />
            <textarea value={text} rows={Math.min(8, Math.max(2, text.split("\n").length))} maxLength={TEXT_MAX} onKeyDown={onKey}
                      onPaste={(e) => {
                        const files = Array.from(e.clipboardData?.items ?? []).filter((it) => it.kind === "file")
                          .map((it) => it.getAsFile()).filter((f): f is File => Boolean(f));
                        if (files.length) { e.preventDefault(); void addPicked(files); }
                      }}
                      onChange={(e) => { setText(e.target.value); setAtOpen(e.target.value.endsWith("@")); }}
                      placeholder={dragOver ? "松手, 把文件加到这条消息里" : "说点什么 · Enter 发送 · @ 提醒某人 · 文件可以直接拖进来 · 要派任务就照常说, 如「@张三 周五前交拜访记录」, 再点「派给@的人」"}
                      style={{ ...inputStyle, resize: "none",
                               ...(dragOver ? { borderColor: "var(--catfish-cyan)", boxShadow: "0 0 0 2px var(--catfish-cyan)" } : {}) }} />
            {asTask && (
              <span style={{ ...muted, color: asTask.problem ? "var(--status-warn, #d97706)" : "inherit" }}>
                {asTask.problem ? `派不了: ${asTask.problem}` : `点「派给@的人」= ${asTask.label}`}
                {asTask.kind === "task" && asTask.expect && !asTask.problem && ` · 要交: ${asTask.expect.length > 30 ? `${asTask.expect.slice(0, 30)}…` : asTask.expect}`}
                {asTask.kind === "task" && !asTask.problem && !(asTask.schedule && autoPost) && " · 结果只回给你, 群里只显示谁接了、谁交了"}
                {expectHint(asTask) && <span style={{ color: "var(--status-warn, #d97706)" }}> · {expectHint(asTask)}</span>}
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
              <span style={{ ...muted, whiteSpace: "nowrap" }} title="长文字请用邮件; 文件点 📎 或直接拖进来 (最多 5 个, 合计 200MB)">{text.length}/{TEXT_MAX}</span>
              <span style={{ display: "flex", gap: 6, whiteSpace: "nowrap", flexShrink: 0 }}>
                <Btn kind="ghost" disabled={Boolean(helping)} onClick={() => setHelpOpen((v) => !v)}>
                  {helping ? "🐟 小鲶在写…" : "🐟 小鲶"}
                </Btn>
                <Btn kind="ghost" onClick={() => setAtOpen((v) => !v)}>@</Btn>
                <AttachButton disabled={busy} onPick={(l) => void addPicked(l)} />
                <ReviseStartButton disabled={busy} onStaged={(src) => setReviseUi({ start: src })} onError={setError} />
                {asTask && (
                  <Btn kind="ghost" disabled={busy || Boolean(asTask.problem)} onClick={() => void dispatch()}>派给@的人</Btn>
                )}
                <Btn kind="primary" disabled={busy || (!text.trim() && !picked.length)} onClick={() => void send()}>{prep !== null ? `准备文件 ${prep}%` : busy ? "发送中…" : "发送"}</Btn>
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
                {owner && joined && !m.me && m.state === "joined" && (
                  <button type="button" aria-label={`把群主转给 ${m.email}`} onClick={() => setModal({ transfer: m })}
                          style={{ border: "none", background: "transparent", cursor: "pointer", fontSize: 11, color: "var(--catfish-text-muted)" }}>设为群主</button>
                )}
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
            <span style={muted}>要退群: 先在上面把「设为群主」给一位已加入的成员。</span>
          </div>
        )}
        {joined && !owner && <div style={{ marginTop: 6 }}><Btn kind="ghost" onClick={() => setModal("leave")}>退出群</Btn></div>}
      </div>}

      {work && <TaskWork taskId={work} onClose={() => { setWork(null); void load(); }} />}
      {formUi && "compose" in formUi && (
        <FormComposer room={{ roomId, name: room.name, members: members.filter((x) => x.state === "joined" && x.email !== me) }}
                      onClose={() => setFormUi(null)} onCreated={(id, n) => { setFormUi({ watch: id, notice: n }); void load(); }} />
      )}
      {formUi && "fill" in formUi && <FormFill formId={formUi.fill} onClose={() => { setFormUi(null); void load(); }} />}
      {formUi && "watch" in formUi && <FormProgress formId={formUi.watch} initialError={formUi.notice} onClose={() => { setFormUi(null); void load(); }} />}
      {watch && <TaskProgress taskId={watch.id} initialError={watch.notice} onClose={() => setWatch(null)} />}
      {reviseUi && "merge" in reviseUi && <ReviseMergePanel roomId={roomId} revId={reviseUi.merge} title={reviseUi.title} onClose={() => setReviseUi(null)}
                                                            onDone={(t) => { setReviseUi(null); setHint(t); void load(); }} />}
      {reviseUi && "versions" in reviseUi && <DocVersions roomId={roomId} docId={reviseUi.versions} onClose={() => setReviseUi(null)} />}
      {reviseUi && "start" in reviseUi && <ReviseStartDialog roomId={roomId} me={me} members={members} source={reviseUi.start}
                                                             onClose={() => setReviseUi(null)} onDone={(t) => { setReviseUi(null); setHint(t); void load(); }} />}
      {modal === "add" && <RoomComposer roomId={roomId} existing={room.members} onClose={() => setModal(null)}
                                        onDone={(_id, n) => { setModal(null); onNotice(n); void load(); }} />}
      {modal === "info" && <InfoEditor room={room} busy={busy} onClose={() => setModal(null)}
                                       onSave={async (n, p, k) => { if (await act(() => roomUpdate(roomId, n, p, k))) setModal(null); }} />}
      {modal === "disband" && <Confirm title="解散群" text={`解散「${room.name}」? 所有成员都会收到通知, 之后谁都不能再发消息。各人电脑上的聊天记录还在。`}
                                       ok="解散" busy={busy} onClose={() => setModal(null)}
                                       onOk={async () => { if (await act(() => roomDisband(roomId))) setModal(null); }} />}
      {modal === "leave" && <Confirm title="退出群" text={`退出「${room.name}」? 群里的人会收到通知, 之后收不到这个群的消息。`}
                                     ok="退出" busy={busy} onClose={() => setModal(null)}
                                     onOk={async () => { if (await act(() => roomLeave(roomId))) setModal(null); }} />}
      {modal && typeof modal === "object" && "remove" in modal && (
        <Confirm title="移出成员" text={`把 ${modal.remove.name || modal.remove.email} 移出「${room.name}」?`} ok="移出" busy={busy}
                 onClose={() => setModal(null)}
                 onOk={async () => { if (await act(() => roomRemove(roomId, modal.remove.email))) setModal(null); }} />
      )}
      {modal && typeof modal === "object" && "transfer" in modal && (
        <Confirm title="转让群主" busy={busy} ok="转让" onClose={() => setModal(null)}
                 text={`把「${room.name}」的群主转给 ${modal.transfer.name || modal.transfer.email}? 之后拉人、移出、改群名和公告、解散都由 TA 来, 你就是普通成员 (也就能退群了)。所有成员都会收到通知。`}
                 onOk={async () => { if (await act(() => roomTransfer(roomId, modal.transfer.email))) setModal(null); }} />
      )}
    </div>
  );
}

function Bubble({ m, mine, members, me, quoted, onReply, roomId, onChanged, onRevise }: {
  m: RoomMessage; mine: boolean; members: RoomMember[]; me: string; quoted?: RoomMessage; onReply?: () => void;
  roomId: string; onChanged: () => void; onRevise?: (src: ReviseSource) => void;
}) {
  const delivery = <DeliveryLine roomId={roomId} msgId={m.msg_id} rows={m.delivery} members={members} hasFiles={Boolean(m.files?.length)}
                                 align={m.kind === "system" ? "center" : mine ? "flex-end" : "flex-start"} onChanged={onChanged} />;
  if (m.kind === "system") {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 1 }}>
        <span style={oneLine}>{m.text} · {timeLabel(m.at)}</span>
        {delivery}
      </div>
    );
  }
  if (m.kind === "auto") {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: mine ? "flex-end" : "flex-start", gap: 2 }}>
        <span style={muted}>🤖 自动转发 · {mine ? "我" : labelOf(members, m.from)} 的电脑 · {timeLabel(m.at)}</span>
        <div style={{ maxWidth: "78%", padding: "6px 10px", borderRadius: 10, fontSize: 13, whiteSpace: "pre-wrap", wordBreak: "break-word",
                      border: "1px dashed var(--catfish-border)", background: "var(--catfish-bg-subtle, rgba(0,0,0,0.03))" }}>
          {m.text}
        </div>
        {delivery}
      </div>
    );
  }
  if (m.kind === "deadline") {   // 11/07 发起人延期
    return <span style={oneLine} title={m.text}>⏳ {m.text} · {timeLabel(m.at)}</span>;
  }
  if (m.kind === "status" || m.kind === "form_status") {
    // 只有那条任务 / 那张表不在眼前 (更早、没显示) 时才单独一行
    const st = m.status?.state ?? m.form_status?.state;
    const icon = st === "done" || st === "submitted" ? "✅" : st === "accepted" ? "🛠" : st === "closed" ? "📋" : "↩️";
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
        {m.files?.length ? <RoomFileList roomId={roomId} files={m.files} mine={mine}
                                         onRevise={onRevise && ((f) => onRevise({ msg_id: m.msg_id, file_id: f.id, name: f.name }))} /> : null}
      </div>
      {delivery}
      <span style={{ display: "flex", gap: 8 }}>
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
function TaskMessage({ m, me, members, messages, busy, local: rec, onAccept, onWork, onWatch, onSummary, roomId, onChanged }: {
  m: RoomMessage; me: string; members: RoomMember[]; messages: RoomMessage[]; busy: boolean;
  /** 派给我的: 我这边这个任务 (状态: 小鲶做好了 = 待核对; 11/07 work = 小鲶在后台做到哪了) */
  local?: IncomingTaskSummary;
  onAccept: () => void; onWork: () => void; onWatch: () => void; onSummary?: () => void;
  roomId: string; onChanged: () => void;
}) {
  const local = rec?.state;
  const c = m.card!;
  const mine = c.owner === me;
  const states = cardStates(c.task_id, c.assignees, messages, members);
  const deadline = latestDeadline(c.task_id, c.deadline, messages);
  const myState = states.find((x) => x.email === me)?.state;
  const link = { border: "none", background: "transparent", cursor: "pointer", fontSize: 11, padding: 0, color: "var(--catfish-cyan)" };
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: mine ? "flex-end" : "flex-start", gap: 2 }}>
      {/* 11/5 鸿波: 状态别单独占一行 —— 跟「谁 · 几点」放同一行 */}
      <span style={{ ...muted, display: "flex", flexWrap: "wrap", gap: 8, justifyContent: mine ? "flex-end" : "flex-start", maxWidth: "90%" }}>
        <span>{mine ? "我" : labelOf(members, m.from)} · {timeLabel(m.at)}</span>
        <span>📋 {c.schedule ? `⏰ ${c.schedule}` : deadline ? `截止 ${shortWhen(deadline)}` : "不限时"}{c.auto_post ? " · 结果自动发群" : ""}</span>
        {states.map((x) => (
          <span key={x.email} title={x.email}
                style={{ color: x.state === "done" ? "var(--status-ok, #059669)" : undefined }}>
            {x.email === me ? "我" : x.name} {x.email === me && local === "待核对" ? "待核对 (小鲶做好了)"
              : x.email === me && local === "在做" && rec?.work !== undefined ? workText(rec.work) : STATE_WORD[x.state]}
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
      <DeliveryLine roomId={roomId} msgId={m.msg_id} rows={m.delivery} members={members} align={mine ? "flex-end" : "flex-start"} onChanged={onChanged} />
    </div>
  );
}
