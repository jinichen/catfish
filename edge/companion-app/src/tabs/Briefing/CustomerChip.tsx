/** 11/3: 问候行里的客户小标签 (鸿波 catch '空间太浪费')。
 *
 * 原来「🤝 客户跟进 · 1 个客户, 今天没有要跟的 ✓」单独占一整行, 那时候没有任何要做的事。
 * 现在没有要跟的卡片时只在问候行里放这个小标签; 有卡片时整条跟进区照常出 (CustomerFollowupStrip),
 * 这里不重复。没有客户时也显示「+ 加客户」—— 原来 0 个客户整条不渲染, 新员工看不出这里能管客户。
 */
import type { CustomerRadarState } from "./useCustomerRadar";

const btn = {
  background: "transparent", border: "none", padding: 0, cursor: "pointer", fontFamily: "inherit", fontSize: 12,
} as const;

export default function CustomerChip({ radar, onAdd }: { radar: CustomerRadarState; onAdd: () => void }) {
  if (radar.error || radar.cards === null || radar.cards.length > 0) return null;
  return (
    <span style={{ display: "inline-flex", gap: 6, alignItems: "center", fontSize: 12, color: "var(--catfish-text-muted)" }}>
      <span>🤝 {radar.customers > 0 ? `${radar.customers} 个客户, 今天没有要跟的 ✓` : "还没有客户"}</span>
      <button type="button" onClick={onAdd} style={{ ...btn, color: "var(--catfish-cyan)" }}>+ 加客户</button>
    </span>
  );
}
