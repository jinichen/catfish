/**
 * 「协同」页 → 群聊 (11/5, docs/GROUP-CHAT-PLAN.md 第一步)。
 *
 * 左: 群列表 (等我加入的排最前, 未读 / @我 红点); 中: 聊天; 右: 成员 + 群主管理 (拉人 / 移出 / 改名和公告 / 解散) / 退群。
 * 一条消息 = 给每个已加入的成员各投一封, 聊天记录只在各自电脑上; 每一封都是员工自己点了才发。
 * 正打开某个群时 3 秒取一次邮筒 (colleagueRoomsStore.setActiveRoom), 平时 20 秒。
 */
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { refreshRooms, setActiveRoom, useColleagueRooms } from "../../lib/colleagueRoomsStore";
import {
  failedNotice, roomDecline, roomDelete, roomDisband, roomGet, roomJoin, roomLeave, roomRead, roomRemove, roomSend, roomUpdate,
  type RoomDetail, type RoomMember, type RoomMessage, type RoomSummary,
} from "../../lib/tauri_colleague_rooms";
import { Overlay, muted } from "./formsUi";
import RoomComposer, { NAME_MAX } from "./RoomComposer";
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

export function timeLabel(at: string, now: Date = new Date()): string {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return "";
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return d.toDateString() === now.toDateString() ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
}

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
    <div style={{ ...cardStyle, height: "auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
        <div>
          <h3 style={{ margin: 0, fontSize: "var(--text-md)", fontWeight: 600 }}>💬 同事群聊</h3>
          <div style={{ ...muted, marginTop: 4 }}>
            几位同事在一个群里说事。消息只存在每个成员自己的电脑上, 中央只转交、看不到内容; 每一条都是你点了才发。
          </div>
        </div>
        <Btn kind="primary" onClick={() => setCreating(true)}>建群</Btn>
      </div>
      {syncError && <span style={muted}>暂时收不到群消息: {syncError}</span>}

      <div style={{ display: "grid", gridTemplateColumns: "260px minmax(0, 1fr)", gap: "var(--space-3)", minHeight: 520 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, overflowY: "auto", maxHeight: 640 }}>
          {loaded && rooms.length === 0 && <span style={muted}>还没有群。点右上角「建群」, 拉几位同事进来。</span>}
          {rooms.map((r) => <RoomRow key={r.room_id} r={r} on={r.room_id === open} onClick={() => { setNotice(undefined); setOpen(r.room_id); }} />)}
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
    r.last ? `${r.last.name}: ${r.last.text}` : `${r.joined} 人已加入`;
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
  const [d, setD] = useState<RoomDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState("");
  const [replyTo, setReplyTo] = useState<RoomMessage | null>(null);
  const [atOpen, setAtOpen] = useState(false);
  const [modal, setModal] = useState<null | "add" | "info" | "disband" | "leave" | { remove: RoomMember }>(null);
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

  async function send() {
    const body = text.trim();
    if (!body) return;
    const ok = await act(() => roomSend(roomId, body, mentionsIn(body, members, me), replyTo?.msg_id), "没收到这条消息");
    if (ok) { setText(""); setReplyTo(null); }
  }
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); }
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 220px", gap: "var(--space-3)", minHeight: 0 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, minHeight: 0 }}>
        <div style={{ borderBottom: "1px solid var(--catfish-border)", paddingBottom: 6 }}>
          <strong style={{ fontSize: "var(--text-sm)" }}>{room.name}</strong>
          <span style={{ ...muted, marginLeft: 8 }}>{room.joined} 人已加入{room.members > room.joined ? ` · ${room.members - room.joined} 人还没加入` : ""}</span>
          {room.purpose && <div style={{ ...muted, marginTop: 4, whiteSpace: "pre-wrap" }}>📌 {room.purpose}</div>}
        </div>
        {notice && <ErrorLine>{notice}</ErrorLine>}
        {error && <ErrorLine>{error}</ErrorLine>}

        <div style={{ flex: 1, overflowY: "auto", maxHeight: 520, display: "flex", flexDirection: "column", gap: 8, paddingRight: 4 }}>
          {d.older > 0 && <span style={{ ...muted, textAlign: "center" }}>更早的 {d.older} 条没显示</span>}
          {d.messages.map((m) => (
            <Bubble key={m.msg_id} m={m} mine={m.from === me} members={members} me={me} quoted={m.reply_to ? byId.get(m.reply_to) : undefined}
                    onReply={joined && m.kind === "text" ? () => setReplyTo(m) : undefined} />
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
          <div style={{ display: "flex", flexDirection: "column", gap: 4, position: "relative" }}>
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
                            display: "flex", flexDirection: "column", maxHeight: 200, overflowY: "auto", minWidth: 180 }}>
                {people.filter((p) => p.email !== me).map((p) => (
                  <button key={p.email} type="button"
                          onClick={() => { setText((t) => `${t}${t.endsWith("@") ? "" : "@"}${p.name || p.email.split("@")[0]} `); setAtOpen(false); }}
                          style={{ textAlign: "left", border: "none", background: "transparent", cursor: "pointer", padding: "4px 8px", fontSize: 12 }}>
                    @{p.name || p.email.split("@")[0]}
                  </button>
                ))}
              </div>
            )}
            <textarea value={text} rows={3} maxLength={TEXT_MAX} onKeyDown={onKey}
                      onChange={(e) => { setText(e.target.value); setAtOpen(e.target.value.endsWith("@")); }}
                      placeholder="说点什么 · Enter 发送, Shift+Enter 换行 · 输入 @ 提醒某人"
                      style={{ ...inputStyle, resize: "vertical" }} />
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={muted}>{text.length}/{TEXT_MAX} · 长材料请用邮件, 这一步还不能传附件</span>
              <span style={{ display: "flex", gap: 6 }}>
                <Btn kind="ghost" onClick={() => setAtOpen((v) => !v)}>@</Btn>
                <Btn kind="primary" disabled={busy || !text.trim()} onClick={() => void send()}>{busy ? "发送中…" : "发送"}</Btn>
              </span>
            </div>
          </div>
        )}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6, borderLeft: "1px solid var(--catfish-border)", paddingLeft: 10 }}>
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
      </div>

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
    return <span style={{ ...muted, textAlign: "center" }}>{m.text} · {timeLabel(m.at)}</span>;
  }
  const atMe = !mine && (m.mentions ?? []).includes(me);
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: mine ? "flex-end" : "flex-start", gap: 2 }}>
      <span style={muted}>{mine ? "我" : labelOf(members, m.from)} · {timeLabel(m.at)}</span>
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
