/**
 * 「协同」tab (P50, 9/10) —— 横向协同的唯一入口。
 *
 * 9/10 鸿波: 最初做成工作台「今日」区两张卡, 一个要填表、等回音、来回点头的流程
 * 塞在看状态的区里, 员工找不到也想不到去那发起 → 独立 tab。
 *
 * 左栏 = 我主动做的事 (请同事帮忙 + 我发出的进度);
 * 右栏 = 等我点头的事 (同事的请求 / 对方小鲶要跑的工具 / 要发回去的回复)。
 * 右栏有货时导航项上有红点 (TabBar 读 roomLinkStore.pendingCount)。
 */
import AskColleaguePanel from "./AskColleaguePanel";
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
        <AskColleaguePanel />
        <PendingPanel />
      </div>
    </div>
  );
}
