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
 * 11/11: 右栏最上面「请你修改」—— 群里请我改、还没交回的文档 (ReviseTodoPanel), 也算红点。
 * 11/5: 顶上一个切换 —— 协同事项 / 群聊 (RoomsPanel, docs/GROUP-CHAT-PLAN.md); 群邀请和群未读也算红点。
 */
import { pendingReviseCount, pendingRoomCount, useColleagueRooms } from "../../lib/colleagueRoomsStore";
import TasksPanel from "./TasksPanel";
import FormsPanel from "./FormsPanel";
import PendingPanel from "./PendingPanel";
import ReviseTodoPanel from "./ReviseTodoPanel";
import RoomsPanel from "./RoomsPanel";
import { useUIStore } from "../../store/ui";

export default function CollabTab() {
  const view = useUIStore((s) => s.collabView);
  const setView = useUIStore((s) => s.setCollabView);
  const rooms = useColleagueRooms();
  const roomBadge = pendingRoomCount(rooms);
  const reviseBadge = pendingReviseCount(rooms);
  const seg = (on: boolean) => ({
    border: "1px solid var(--catfish-border)", cursor: "pointer", font: "inherit", fontSize: 13,
    padding: "6px 14px", borderRadius: "var(--radius-sm, 6px)",
    background: on ? "var(--catfish-cyan)" : "transparent", color: on ? "white" : "inherit",
    display: "inline-flex", alignItems: "center", gap: 6,
  });
  return (
    // 11/5 鸿波: 两个视图都只占一屏, 里面的列表自己滚 (App 锁住了 main 的滚动)
    <div style={{ height: "100%", boxSizing: "border-box", display: "flex", flexDirection: "column", padding: "var(--space-3) var(--space-4)" }}>
      <div role="tablist" style={{ display: "flex", gap: 8, marginBottom: "var(--space-3)", flexShrink: 0 }}>
        <button type="button" role="tab" aria-selected={view === "work"} style={seg(view === "work")} onClick={() => setView("work")}>
          协同事项
          {reviseBadge > 0 && (
            <span aria-label={`${reviseBadge} 份文档等你修改`}
                  style={{ fontSize: 10, minWidth: 16, padding: "0 5px", borderRadius: 8, background: "var(--status-error, #dc2626)", color: "white" }}>
              {reviseBadge}
            </span>
          )}
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
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 3fr) minmax(0, 2fr)", gridTemplateRows: "minmax(0, 1fr)",
                      gap: "var(--space-4)", flex: 1, minHeight: 0 }}>
          {/* 左: 请同事帮忙 (列表自己滚) */}
          <TasksPanel />
          {/* 右: 等我点头 (多半是空的, 最多占四成) + 一起填表, 各自滚 */}
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)", minHeight: 0 }}>
            {rooms.revise.length > 0 && (   // 11/11 群里请我改的文档 (没有就不占地方)
              <div style={{ flexShrink: 0, maxHeight: "35%", overflowY: "auto", borderRadius: "var(--radius-md)" }}><ReviseTodoPanel /></div>
            )}
            <div style={{ flexShrink: 0, maxHeight: "40%", overflowY: "auto", borderRadius: "var(--radius-md)" }}><PendingPanel /></div>
            <div style={{ flex: 1, minHeight: 0, overflowY: "auto", borderRadius: "var(--radius-md)" }}><FormsPanel /></div>
          </div>
        </div>
      )}
    </div>
  );
}
