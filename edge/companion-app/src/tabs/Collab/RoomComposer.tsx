/**
 * 建群 / 群主拉人 (11/5, docs/GROUP-CHAT-PLAN.md 第一步)。
 *
 * 选同事复用 RecipientPicker; 从通讯录点选的同事带上姓名, 群里显示名字。对方收到邀请后自己点「加入」才进群。
 * 一个群最多 50 人 (含自己)。
 */
import { useCallback, useState } from "react";

import { refreshRooms } from "../../lib/colleagueRoomsStore";
import { failedNotice, roomAdd, roomCreate, type PersonIn } from "../../lib/tauri_colleague_rooms";
import type { Person } from "../../lib/tauri_forms";
import { Overlay, muted } from "./formsUi";
import RecipientPicker, { allRecipients } from "./RecipientPicker";
import { Btn, ErrorLine, inputStyle } from "./roomLinkUi";

export const MAX_MEMBERS = 50;
export const NAME_MAX = 30;

/** 邮箱 → 带名字的成员 (通讯录里点选过的才有名字)。 */
export function withNames(emails: string[], names: Record<string, string>): PersonIn[] {
  return emails.map((email) => ({ email, name: names[email] ?? "" }));
}

export default function RoomComposer({ roomId, existing = 0, onClose, onDone }: {
  /** 有 = 给这个群拉人; 没有 = 建新群 */
  roomId?: string;
  /** 拉人时群里已有的人数 (算上限) */
  existing?: number;
  onClose: () => void;
  onDone: (roomId: string, notice?: string) => void;
}) {
  const adding = Boolean(roomId);
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [typed, setTyped] = useState("");
  const [names, setNames] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const onPickError = useCallback((m: string) => setError(m), []);
  const onPerson = useCallback((p: Person) => setNames((n) => ({ ...n, [p.email.toLowerCase()]: p.name })), []);

  const all = allRecipients(picked, typed);
  const total = (adding ? existing : 1) + all.length;
  const canSend = !busy && all.length > 0 && total <= MAX_MEMBERS && (adding || name.trim().length > 0);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const members = withNames(all, names);
      const r = adding ? await roomAdd(roomId as string, members) : await roomCreate(name.trim(), purpose.trim(), members);
      void refreshRooms();
      onDone(r.room.room_id, failedNotice(r.failed, "没收到邀请"));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Overlay title={adding ? "拉同事进群" : "建群"} onClose={onClose} width={640}>
      <span style={muted}>
        群消息只存在每个成员自己的电脑上, 中央只转交、取走即清空。对方收到邀请后自己点「加入」才进群, 加入前的聊天记录看不到。
      </span>
      {!adding && (
        <>
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={NAME_MAX} placeholder="群名, 如「华东项目群」"
                 style={inputStyle} />
          <textarea value={purpose} onChange={(e) => setPurpose(e.target.value)} rows={2} maxLength={500}
                    placeholder="群公告 / 用途 (可不填), 如「华东区客户跟进, 每周五交周报」" style={{ ...inputStyle, resize: "vertical" }} />
        </>
      )}
      <strong style={{ fontSize: 13 }}>{adding ? "拉谁进群" : "拉谁进群"} (群里最多 {MAX_MEMBERS} 人, 含你自己)</strong>
      <RecipientPicker picked={picked} onPicked={setPicked} typed={typed} onTyped={setTyped} onError={onPickError} onPerson={onPerson} />
      {total > MAX_MEMBERS && <ErrorLine>群里最多 {MAX_MEMBERS} 人, 现在 {total} 人</ErrorLine>}
      {error && <ErrorLine>{error}</ErrorLine>}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <Btn kind="ghost" onClick={onClose}>取消</Btn>
        <Btn kind="primary" disabled={!canSend} onClick={submit}>
          {busy ? "发送中…" : adding ? `邀请 ${all.length || ""} 位同事` : `建群并邀请 ${all.length || ""} 位同事`}
        </Btn>
      </div>
    </Overlay>
  );
}
