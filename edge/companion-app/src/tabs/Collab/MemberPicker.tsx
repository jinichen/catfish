/**
 * 在群里派任务时选人 (11/5, docs/GROUP-CHAT-PLAN.md 第二步): 只从群里**已加入**的人里勾 (不含自己)。
 * 没加入的人看不到任务卡、也不在群消息里, 不让选。
 */
export interface RoomCtx {
  roomId: string;
  name: string;
  /** 群里已加入的其他人 */
  members: { email: string; name: string }[];
}

export default function MemberPicker({ room, picked, onPicked }: {
  room: RoomCtx;
  picked: string[];
  onPicked: (v: string[]) => void;
}) {
  if (!room.members.length) {
    return <span style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>群里还没有其他人加入, 等同事点了「加入」再派。</span>;
  }
  const all = room.members.every((m) => picked.includes(m.email));
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
      <label style={{ fontSize: 12, display: "inline-flex", gap: 4, alignItems: "center", cursor: "pointer" }}>
        <input type="checkbox" checked={all} onChange={() => onPicked(all ? [] : room.members.map((m) => m.email))} />
        全部
      </label>
      {room.members.map((m) => (
        <label key={m.email} title={m.email}
               style={{ fontSize: 12, display: "inline-flex", gap: 4, alignItems: "center", cursor: "pointer" }}>
          <input type="checkbox" checked={picked.includes(m.email)}
                 onChange={() => onPicked(picked.includes(m.email) ? picked.filter((x) => x !== m.email) : [...picked, m.email])} />
          {m.name || m.email.split("@")[0]}
        </label>
      ))}
    </div>
  );
}
