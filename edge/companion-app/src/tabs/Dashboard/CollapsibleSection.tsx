/** Dashboard 折叠分组 — BL-D-DASH (5/7 ship).
 *
 * 鸿波 5/7 反馈"仪表盘内容太多". 18 张卡分 5-7 组, 每组可折叠.
 * localStorage 记员工偏好 (key: dashboard_section_<id>_collapsed).
 *
 * Props:
 *   id: 唯一标识, localStorage key 用
 *   title: 标题文字 (例 "🔥 今日")
 *   defaultCollapsed: 首次访问时是否折叠 (员工后续手动展开/收起会覆盖)
 *   children: 卡片们
 *   gridSpan: 该组内部 grid 列数 (默认 2, 跟 Dashboard 主 grid 对齐)
 */
import { useState, ReactNode } from "react";

interface Props {
  id: string;
  title: string;
  defaultCollapsed?: boolean;
  children: ReactNode;
  count?: number;  // 显示卡数量, 折叠时也能看到
}

const STORAGE_PREFIX = "dashboard_section_";

function readCollapsed(id: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(`${STORAGE_PREFIX}${id}_collapsed`);
    if (v === null) return fallback;
    return v === "1";
  } catch {
    return fallback;
  }
}

function writeCollapsed(id: string, collapsed: boolean): void {
  try {
    localStorage.setItem(`${STORAGE_PREFIX}${id}_collapsed`, collapsed ? "1" : "0");
  } catch {
    // localStorage 满 / 禁用 — silent fail, 不影响 UI
  }
}

export default function CollapsibleSection({
  id,
  title,
  defaultCollapsed = false,
  children,
  count,
}: Props) {
  const [collapsed, setCollapsed] = useState(() =>
    readCollapsed(id, defaultCollapsed)
  );

  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    writeCollapsed(id, next);
  };

  return (
    <div
      style={{
        gridColumn: "1 / -1",
        marginTop: "var(--space-2)",
      }}
    >
      <button
        onClick={toggle}
        type="button"
        style={{
          width: "100%",
          background: "transparent",
          border: "none",
          padding: "var(--space-2) 0",
          fontSize: "var(--text-sm)",
          fontWeight: 600,
          color: "var(--catfish-text)",
          textAlign: "left",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          borderBottom: "1px solid var(--catfish-border)",
          marginBottom: "var(--space-3)",
        }}
        aria-expanded={!collapsed}
      >
        <span
          style={{
            display: "inline-block",
            transform: collapsed ? "rotate(-90deg)" : "rotate(0deg)",
            transition: "transform 0.15s ease",
            fontSize: "10px",
          }}
        >
          ▼
        </span>
        <span>{title}</span>
        {count !== undefined && (
          <span
            style={{
              fontSize: "var(--text-xs)",
              color: "var(--catfish-text-muted)",
              fontWeight: 400,
            }}
          >
            · {count} 项
          </span>
        )}
      </button>
      {!collapsed && (
        <div
          style={{
            display: "grid",
            // BL-FIX19 (5/8): minmax(0, 1fr) 不是 1fr — 默认 grid item min size
            // 是 'auto' = min-content, 长 URL / 长路径会撑爆 cell 让 grid 变宽.
            // minmax(0, 1fr) 强制最小 0, cells 平分剩余空间, 内容自动 wrap.
            gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
            gap: "var(--space-4)",
          }}
        >
          {children}
        </div>
      )}
    </div>
  );
}
