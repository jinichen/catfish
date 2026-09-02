/** Dashboard 折叠分组 — BL-D-DASH (5/7 ship).
 *
 * 鸿波 5/7 反馈"仪表盘内容太多". 18 张卡分 5-7 组, 每组可折叠.
 * localStorage 记员工偏好 (key: dashboard_section_<id>_collapsed).
 *
 * Props:
 *   id: 唯一标识, localStorage key 用
 *   title: 标题内容 (可包含规范图标和标题文字)
 *   defaultCollapsed: 首次访问时是否折叠 (员工后续手动展开/收起会覆盖)
 *   children: 卡片们
 *   gridSpan: 该组内部 grid 列数 (默认 2, 跟 Dashboard 主 grid 对齐)
 */
import { useState, ReactNode } from "react";

interface Props {
  id: string;
  title: ReactNode;
  defaultCollapsed?: boolean;
  children: ReactNode;
  /** BL-SECTION-COUNT-KILL (5/16): count prop 仍接收但不再渲染. 调用方传值不影响.
   *  当时为了 5 类 widget 看起来有结构感, 砍卡后 count 一直变, 维护成本反而出来,
   *  而且 dashboard widget 计数对员工没产品价值 (不是 list count). */
  count?: number;
  /** BL-SECTION-MAX-COLS (6/1 鸿波): 限最大列数. 默认 undefined = auto-fit
   *  (当前所有 section 都是这模式). 鲶鱼对你的认识 4 卡传 2 → 2x2 整齐, 不
   *  会 3 列第二行孤零零. */
  maxColumns?: number;
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
  count: _count,  // BL-SECTION-COUNT-KILL: 接收不渲染 (兼容现有调用方传 count)
  maxColumns,
}: Props) {
  const [collapsed, setCollapsed] = useState(() =>
    readCollapsed(id, defaultCollapsed)
  );

  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    writeCollapsed(id, next);
  };

  // 6/8 BL-SECTION-SIDEBAR-LAYOUT (鸿波 6/8): section 改 macOS Settings 风格
  // sidebar layout. 标题在左 (~180px), 卡在右 (flex). 视觉跟 PrivacyCard 内
  // Row 一致. collapsed 时退回单行 button (省纵向空间).
  //
  // - collapsed: full-width button, 单行 ▶ + title (跟原行为一致, 不改).
  // - expanded:  grid 2 列, 左 sidebar (button 仍 toggle) + 右 cards grid.

  const TitleButton = (
    <button
      onClick={toggle}
      type="button"
      style={{
        width: "100%",
        background: "transparent",
        border: "none",
        // collapsed 时画底线 (区隔下一 section), expanded 时不画 (左 sidebar 不需要)
        borderBottom: collapsed ? "1px solid var(--catfish-border)" : "none",
        padding: collapsed ? "8px 0" : "8px 0 8px 0",
        fontSize: 14,
        fontWeight: 600,
        color: "var(--catfish-text)",
        textAlign: "left",
        cursor: "pointer",
        display: "flex",
        alignItems: collapsed ? "center" : "flex-start",
        gap: "8px",
        marginBottom: collapsed ? "var(--space-3)" : 0,
      }}
      aria-expanded={!collapsed}
    >
      <span
        style={{
          display: "inline-block",
          transform: collapsed ? "rotate(-90deg)" : "rotate(0deg)",
          transition: "transform 0.15s ease",
          fontSize: 10,
          opacity: 0.5,
          // expanded 时三角跟标题首行对齐
          marginTop: collapsed ? 0 : 6,
          flexShrink: 0,
        }}
        aria-hidden
      >
        ▼
      </span>
      <span style={{ lineHeight: 1.4 }}>{title}</span>
    </button>
  );

  return (
    <section
      style={{
        gridColumn: "1 / -1",
        marginTop: "var(--space-5)",
      }}
    >
      {collapsed ? (
        // collapsed: 单行 header 占整宽, 跟历史一致
        TitleButton
      ) : (
        // expanded: sidebar 风格 — 左 180px (title + toggle), 右 grid (cards)
        <div
          style={{
            display: "grid",
            // 180px sidebar 跟 PrivacyCard 内 Row 一致 (统一视觉节奏).
            // 窄屏 (< 720px) fallback 到单列 (sidebar 占整行), 防小屏挤碎.
            gridTemplateColumns: "180px 1fr",
            gap: "var(--space-4)",
            // 顶部薄分割线 (替代原 button border-bottom), 视觉分隔 section.
            paddingTop: "var(--space-2)",
            borderTop: "1px solid var(--catfish-border)",
          }}
        >
          {TitleButton}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: maxColumns
                ? `repeat(${maxColumns}, minmax(0, 1fr))`
                : "repeat(auto-fit, minmax(280px, 1fr))",
              gap: "var(--space-3)",
              minWidth: 0,
            }}
          >
            {children}
          </div>
        </div>
      )}
    </section>
  );
}
