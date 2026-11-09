/**
 * 群里发的表 (11/09, 鸿波「填表能力和群聊的能力有没重叠?」—— 差的就是「在群里发表」这一步)。
 *
 * 跟群里派任务一个样子: 消息就是「请填《…》」那句话, 下面一行每人的状态 (还没交 / 已交 / 不填) + 自己能点的按钮:
 * 被点名的人「去填」(打开填表弹层, 表本身走填表那条, 内容只回发起人); 发起人「看进度」。
 * 状态是收件人点「提交 / 不填」、发起人点「结束收集」时自动回群的, 填的内容不进群。
 */
import type { RoomMember, RoomMessage } from "../../lib/tauri_colleague_rooms";
import { DeliveryLine } from "./RoomsDelivery";
import { FORM_STATE_WORD, formStates } from "./roomForms";
import { labelOf, shortWhen, timeLabel } from "./RoomsPanel";
import { muted } from "./formsUi";

const link = { border: "none", background: "transparent", cursor: "pointer", fontSize: 11, padding: 0, color: "var(--catfish-cyan)" };

export default function RoomFormMessage({ m, me, members, messages, roomId, onFill, onWatch, onChanged }: {
  m: RoomMessage; me: string; members: RoomMember[]; messages: RoomMessage[]; roomId: string;
  /** 我是收件人: 打开填表 */
  onFill: (formId: string) => void;
  /** 我是发起人: 看进度 */
  onWatch: (formId: string) => void;
  onChanged: () => void;
}) {
  const c = m.form!;
  const mine = c.owner === me;
  const { people, closed } = formStates(c.form_id, c.recipients, messages, members);
  const my = people.find((p) => p.email === me);
  const done = people.filter((p) => p.state === "submitted").length;
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: mine ? "flex-end" : "flex-start", gap: 2 }}>
      <span style={{ ...muted, display: "flex", flexWrap: "wrap", gap: 8, justifyContent: mine ? "flex-end" : "flex-start", maxWidth: "90%" }}>
        <span>{mine ? "我" : labelOf(members, m.from)} · {timeLabel(m.at)}</span>
        <span>📋 {closed ? "已结束收集" : c.deadline ? `截止 ${shortWhen(c.deadline)}` : "不限时"} · 已交 {done}/{people.length}</span>
        {people.map((p) => (
          <span key={p.email} title={p.email} style={{ color: p.state === "submitted" ? "var(--status-ok, #059669)" : undefined }}>
            {p.email === me ? "我" : p.name} {FORM_STATE_WORD[p.state]}
          </span>
        ))}
        {my && !closed && (
          <button type="button" style={link} onClick={() => onFill(c.form_id)}>{my.state === "waiting" ? "去填" : "看我填的"}</button>
        )}
        {mine && <button type="button" style={link} onClick={() => onWatch(c.form_id)}>看进度</button>}
      </span>
      <div style={{ maxWidth: "78%", padding: "6px 10px", borderRadius: 10, fontSize: 13, whiteSpace: "pre-wrap", wordBreak: "break-word",
                    background: mine ? "var(--catfish-cyan)" : "var(--catfish-bg-subtle, rgba(0,0,0,0.05))", color: mine ? "white" : "inherit",
                    border: my && my.state === "waiting" && !closed ? "1px solid var(--status-warn, #d97706)" : "1px solid transparent",
                    opacity: closed || (my && my.state !== "waiting") ? 0.75 : 1 }}>
        {m.text}
      </div>
      <DeliveryLine roomId={roomId} msgId={m.msg_id} rows={m.delivery} members={members} align={mine ? "flex-end" : "flex-start"} onChanged={onChanged} />
    </div>
  );
}
