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
 */
import TasksPanel from "./TasksPanel";
import FormsPanel from "./FormsPanel";
import PendingPanel from "./PendingPanel";

export default function CollabTab() {
  return (
    <div style={{ maxWidth: 1600, margin: "0 auto", padding: "var(--space-4)" }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
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
    </div>
  );
}
