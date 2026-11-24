/**
 * 群聊左栏: 群列表 (11/24 从 RoomsPanel 拆出来)。
 *
 * 11/24 鸿波「群聊分组多的话…」: 列表放宽到 240 (原 200, 群名 + 最后一句话两个字就截断);
 * 群多于 8 个时顶上出「找群」框 —— 按群名、群用途、最后一条消息 (连说话人) 过滤, 不区分大小写。
 * 右上角那个「搜索」是搜当前群里的消息, 跟这个不是一回事。
 */
import { useMemo, useState } from "react";
import { MagnifyingGlass, X } from "@phosphor-icons/react";

import type { RoomSummary } from "../../lib/tauri_colleague_rooms";
import { muted } from "./formsUi";
import { Btn } from "./roomLinkUi";

/** 群多于这个数才出「找群」框 */
export const ROOM_FILTER_MIN = 8;
export const ROOM_LIST_WIDTH = 240;

export const ENDED_LABEL: Record<string, string> = {
  declined: "你没加入这个群", left: "你已退出这个群", removed: "你已被群主移出这个群", disbanded: "群主已解散这个群",
};

export function lastPreview(last: { name: string; text: string; kind?: string }): string {
  const text = last.text.replace(/^\p{Extended_Pictographic}️?\s*/u, ""); // 「🐟 小鲶: …」列表里不显示开头的 emoji
  return last.kind === "status" || last.kind === "auto" || last.kind === "deadline" || last.kind === "form_status" || last.kind === "revise_status" ? text : `${last.name}: ${text}`;
}

/** 找群: 群名 / 用途 / 最后一条 (说话人 + 内容) 里有这几个字就算 */
export function filterRooms(rooms: RoomSummary[], q: string): RoomSummary[] {
  const k = q.trim().toLowerCase();
  if (!k) return rooms;
  return rooms.filter((r) =>
    [r.name, r.purpose, r.last?.name, r.last?.text].some((s) => (s ?? "").toLowerCase().includes(k)));
}

const listButton = (on: boolean) => ({
  textAlign: "left" as const, cursor: "pointer", font: "inherit", color: "inherit", width: "100%",
  border: `1px solid ${on ? "color-mix(in srgb, var(--catfish-cyan) 40%, var(--catfish-border))" : "var(--catfish-border)"}`,
  borderRadius: "var(--radius-sm, 6px)", padding: "8px 12px",
  background: on ? "var(--catfish-cyan-tint)" : "transparent",
  display: "flex", flexDirection: "column" as const, gap: 2, flexShrink: 0,
});

function Badge({ n, at }: { n: number; at?: boolean }) {
  if (n <= 0) return null;
  return (
    <span style={{ fontSize: 11, lineHeight: "16px", minWidth: 16, padding: "0 4px", borderRadius: 8, textAlign: "center",
                   background: at ? "var(--catfish-cyan)" : "var(--status-err)", color: "white", fontVariantNumeric: "tabular-nums" }}>
      {at ? "@" : ""}{n > 99 ? "99+" : n}
    </span>
  );
}

function RoomRow({ r, on, onClick }: { r: RoomSummary; on: boolean; onClick: () => void }) {
  const sub = r.my_state === "invited" ? "邀请你加入" : r.my_state !== "joined" ? ENDED_LABEL[r.my_state] :
    r.last ? lastPreview(r.last) : `${r.joined} 人已加入`;
  return (
    <button type="button" onClick={onClick} style={listButton(on)} title={r.name}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", minWidth: 0 }}>
        <span style={{ fontSize: "var(--text-sm)", fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {r.name}
        </span>
        <span style={{ display: "flex", gap: 4, flexShrink: 0 }}>
          {r.my_state === "invited" && <Badge n={1} />}
          <Badge n={r.mentioned} at />
          <Badge n={r.unread - r.mentioned} />
        </span>
      </div>
      <span style={{ ...muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{sub}</span>
    </button>
  );
}

export default function RoomList({ rooms, loaded, open, onOpen, onCreate }: {
  rooms: RoomSummary[]; loaded: boolean; open: string | null; onOpen: (id: string) => void; onCreate: () => void;
}) {
  const [q, setQ] = useState("");
  const many = rooms.length > ROOM_FILTER_MIN;
  const shown = useMemo(() => (many ? filterRooms(rooms, q) : rooms), [rooms, q, many]);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, minHeight: 0 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexShrink: 0 }}>
        <strong style={{ fontSize: 13 }} title="消息只存在每个成员自己的电脑上, 中央只转交加密内容、看不到原文, 取走即清空, 不保存记录; 每一条都是你点了才发">
          群聊{many && <span style={{ ...muted, fontWeight: 400, marginLeft: 6 }}>{rooms.length}</span>}
        </strong>
        <Btn kind="ghost" onClick={onCreate}>＋ 建群</Btn>
      </div>
      {many && (
        <label className="room-find" style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0, height: 32, padding: "0 8px",
                        border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-sm, 6px)", background: "var(--catfish-bg-elevated)" }}>
          <MagnifyingGlass size={14} aria-hidden style={{ color: "var(--catfish-text-muted)", flexShrink: 0 }} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="找群" aria-label="找群: 按群名或最后一条消息"
                 onKeyDown={(e) => { if (e.key === "Escape") setQ(""); if (e.key === "Enter" && shown[0]) onOpen(shown[0].room_id); }}
                 style={{ flex: 1, minWidth: 0, border: "none", outline: "none", background: "transparent", font: "inherit", fontSize: 12, color: "inherit" }} />
          {q && (
            <button type="button" aria-label="清空" onClick={() => setQ("")}
                    style={{ border: "none", background: "transparent", padding: 0, cursor: "pointer", color: "var(--catfish-text-muted)", display: "flex" }}>
              <X size={14} />
            </button>
          )}
        </label>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 6, overflowY: "auto", minHeight: 0, flex: 1 }}>
        {loaded && rooms.length === 0 && <span style={muted}>还没有群。点「建群」, 拉几位同事进来。</span>}
        {rooms.length > 0 && shown.length === 0 && <span style={muted}>没有名字或最近消息里带「{q.trim()}」的群</span>}
        {shown.map((r) => <RoomRow key={r.room_id} r={r} on={r.room_id === open} onClick={() => onOpen(r.room_id)} />)}
      </div>
    </div>
  );
}
