/** 分诊筛选 chips: 全部 / 办 / 回 (8/21)。
 *
 * 放「仅未读」同一行 —— 左栏 340px 是临界宽度 (P3.5.204.f 教训, 别新开一行
 * 也别塞长文案)。「知」不做筛选项: 知悉类占大多数, 筛它等于看全部。
 */
import type { ActionFilter } from "../../../lib/emailActionFilter";

const OPTIONS: Array<{ key: ActionFilter; label: string; title: string }> = [
  { key: "全部", label: "全部", title: "按时间排列" },
  { key: "办", label: "办", title: "只看要办事的, 截止日近的在前" },
  { key: "回", label: "回", title: "只看要回信的" },
];

export default function ActionFilterChips({
  value,
  counts,
  onChange,
}: {
  value: ActionFilter;
  /** {办: n, 回: n} — 显示在 chip 上, 让员工不点也知道有没有 */
  counts: Record<string, number>;
  onChange: (v: ActionFilter) => void;
}) {
  return (
    <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
      {OPTIONS.map((o) => {
        const active = value === o.key;
        const n = o.key === "全部" ? undefined : counts[o.key] || 0;
        return (
          <button
            key={o.key}
            type="button"
            title={o.title}
            onClick={() => onChange(o.key)}
            style={{
              fontSize: 10,
              padding: "1px 7px",
              borderRadius: 9,
              cursor: "pointer",
              fontFamily: "inherit",
              border: active
                ? "1px solid var(--catfish-cyan)"
                : "1px solid var(--catfish-border)",
              background: active ? "rgba(34, 211, 238, 0.12)" : "transparent",
              color: active ? "var(--catfish-cyan)" : "var(--catfish-text-muted)",
              fontWeight: active ? 600 : 400,
            }}
          >
            {o.label}
            {n !== undefined && n > 0 ? ` ${n}` : ""}
          </button>
        );
      })}
    </div>
  );
}
