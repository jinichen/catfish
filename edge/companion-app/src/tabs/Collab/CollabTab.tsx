/**
 * 「协同」tab (P50, 9/10) —— 横向协同的唯一入口。
 *
 * 9/10 鸿波: 最初做成工作台「今日」区两张卡, 一个要填表、等回音、来回点头的流程
 * 塞在看状态的区里, 员工找不到也想不到去那发起 → 独立 tab。
 *
 * 左栏 = 我主动做的事 (请同事帮忙 + 我发出的进度);
 * 右栏 = 等我点头的事 (同事的请求 / 对方小鲶要跑的工具 / 要发回去的回复)。
 * 右栏有货时导航项上有红点 (TabBar 读 roomLinkStore.pendingCount)。
 *
 * 10/17: 下面一整行 = 一起填表 (FormsPanel, docs/FORM-COLLECT-PLAN.md); 收到要填的表也算红点。
 * 11/4: 左栏换成派任务给多位同事 (TasksPanel, docs/COLLEAGUE-TASK-PLAN.md), 不再要求双方同时在线;
 *   同事派给我、还没交回的也算红点。右栏照旧接老版本同事的在线请求和工具审批。
 * 11/5: 顶上一个切换 —— 协同事项 / 群聊 (RoomsPanel, docs/GROUP-CHAT-PLAN.md); 群邀请和群未读也算红点。
 */
import { pendingRoomCount, useColleagueRooms } from "../../lib/colleagueRoomsStore";
import TasksPanel from "./TasksPanel";
import FormsPanel from "./FormsPanel";
import PendingPanel from "./PendingPanel";
import RoomsPanel from "./RoomsPanel";
import { useUIStore } from "../../store/ui";

export default function CollabTab() {
  const view = useUIStore((s) => s.collabView);
  const setView = useUIStore((s) => s.setCollabView);
  const roomBadge = pendingRoomCount(useColleagueRooms());
  const seg = (on: boolean) => ({
    border: "1px solid var(--catfish-border)", cursor: "pointer", font: "inherit", fontSize: 13,
    padding: "6px 14px", borderRadius: "var(--radius-sm, 6px)",
    background: on ? "var(--catfish-cyan)" : "transparent", color: on ? "white" : "inherit",
    display: "inline-flex", alignItems: "center", gap: 6,
  });
  return (
    <div style={view === "rooms"
      ? { height: "100%", boxSizing: "border-box", display: "flex", flexDirection: "column", padding: "var(--space-3) var(--space-4)" }
      : { maxWidth: 1600, margin: "0 auto", padding: "var(--space-4)" }}>
      <div role="tablist" style={{ display: "flex", gap: 8, marginBottom: view === "rooms" ? "var(--space-3)" : "var(--space-4)", flexShrink: 0 }}>
        <button type="button" role="tab" aria-selected={view === "work"} style={seg(view === "work")} onClick={() => setView("work")}>
          协同事项
        </button>
        <button type="button" role="tab" aria-selected={view === "rooms"} style={seg(view === "rooms")} onClick={() => setView("rooms")}>
          群聊
          {roomBadge > 0 && (
            <span aria-label={`${roomBadge} 条群消息等你看`}
                  style={{ fontSize: 10, minWidth: 16, padding: "0 5px", borderRadius: 8, background: "var(--status-error, #dc2626)", color: "white" }}>
              {roomBadge > 99 ? "99+" : roomBadge}
            </span>
          )}
        </button>
      </div>
      {view === "rooms" ? <RoomsPanel /> : (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 3fr) minmax(0, 2fr)",   // 右边「等我点头」多半是空的, 别占一半
              gap: "var(--space-4)",
              alignItems: "start",
            }}
          >
            <TasksPanel />
            <PendingPanel />
          </div>
          <div style={{ marginTop: "var(--space-4)" }}>
            <FormsPanel />
          </div>
        </>
      )}
    </div>
  );
}
