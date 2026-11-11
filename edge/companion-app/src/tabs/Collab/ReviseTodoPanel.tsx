/**
 * 「协同事项」里的「请你修改」(11/11): 各群里请我改、我还没交回的文档, 截止早的在前。
 * 群消息一多卡片就被刷上去了, 这里不会丢。没有待办时整块不显示。
 * 「下载修改 / 接着改」就地做; 交回要过「文件还开着吗 / 一点没改」两道确认, 那套在群里的卡片上, 这里点「去群里交回」跳过去。
 */
import { useState } from "react";

import { refreshRevisePending, useColleagueRooms } from "../../lib/colleagueRoomsStore";
import { dueLabel } from "../../lib/revisePending";
import { openFile } from "../../lib/tauri_app";
import { reviseOpen, type RevisePendingItem } from "../../lib/tauri_room_revise";
import { useUIStore } from "../../store/ui";
import { muted } from "./formsUi";
import { cardStyle, ErrorLine, itemStyle } from "./roomLinkUi";

const link = { border: "none", background: "transparent", cursor: "pointer", fontSize: 12, padding: 0, color: "var(--catfish-cyan)" } as const;

export default function ReviseTodoPanel() {
  const items = useColleagueRooms().revise;
  const openRoom = useUIStore((s) => s.openRoom);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!items.length) return null;
  async function download(x: RevisePendingItem) {
    setBusy(x.rev_id);
    setError(null);
    try {
      const r = await reviseOpen(x.room_id, x.rev_id);
      await openFile(r.path).catch(() => undefined);
      void refreshRevisePending();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }
  return (
    <div style={{ ...cardStyle, height: "auto", gap: 8 }}>
      <strong style={{ fontSize: 13 }}>📝 请你修改 ({items.length})</strong>
      {error && <ErrorLine>{error}</ErrorLine>}
      {items.map((x) => {
        const urgent = x.days_left !== null && x.days_left <= 1;
        return (
          <div key={x.rev_id} style={{ ...itemStyle, display: "flex", flexDirection: "column", gap: 4 }}>
            <span style={{ fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={x.title}>
              《{x.title}》v{x.version}
            </span>
            <span style={{ ...muted, display: "flex", flexWrap: "wrap", gap: 8 }}>
              <span>{x.owner_name} 请你改 · 群「{x.room_name}」</span>
              <span style={{ color: urgent ? "var(--status-warn, #d97706)" : undefined, fontWeight: urgent ? 600 : 400 }}>{dueLabel(x.days_left)}</span>
              {x.state === "editing" && <span>你在改</span>}
            </span>
            <span style={{ display: "flex", gap: 12 }}>
              <button type="button" style={link} disabled={busy === x.rev_id} onClick={() => void download(x)}>
                {busy === x.rev_id ? "打开中…" : x.local_path ? "接着改" : "下载修改"}
              </button>
              <button type="button" style={link} onClick={() => openRoom(x.room_id)}>去群里交回</button>
            </span>
          </div>
        );
      })}
    </div>
  );
}
