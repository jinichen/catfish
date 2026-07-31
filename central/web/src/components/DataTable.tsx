/** 后台通用紧凑表格 + 徽章 + 按钮 (7/30 三区布局第三步).
 *
 * ## 为什么要抽这个
 *
 * 抽之前, 7 张表各写各的 th/td 样式, 同类数据的行高实测落在
 * **22 / 25 / 29 / 31 / 35 / 37px 六个不同值**上。UsersPage 甚至在同一张表里
 * th 用 padding 6px、td 用 4px。徽章有 6 份独立实现, 按钮样式有 5 份。
 *
 * 这不只是"不好看"。它的实际后果是密度改造做不动 —— 每改一个页面都要
 * 重新决定一次行高该是多少、徽章该长什么样, 于是每页的结论都不一样,
 * 下一个人接着写第 8 套。
 *
 * ## 尺寸从哪来
 *
 * **不是新发明的。** 全部照 QuotaConfigPage 那套 —— 那一页在 6/23 被专门
 * compact 化过两轮 (P3.5.93.1 / .2), 是现存唯一有明确密度结论的页:
 *
 *   fontSize 12 · td padding "4px 8px" · 表头 11px uppercase muted ·
 *   按钮 padding "2px 8px" fontSize 11 · 行分隔线 --border-soft
 *
 * 实测行高约 29px。作为对比, 最松的 AccessPage 是 37px —— 同样 715px 的
 * 可视高度, 29px 能多放 7 行。
 *
 * ## 一个必须说明的坑
 *
 * `--border-soft` 在 7/30 之前**从没被定义过**。CSS 引用未定义变量且无兜底值时
 * 整条声明失效, 所以 QuotaConfigPage 的行分隔线一直不存在, 而且不报错。
 * 现在它在 index.html 里了, 并有 scripts/check-css-vars.mjs 盯着。
 */

import type { CSSProperties, ReactNode } from "react";

// ── 尺寸常量 ────────────────────────────────────────────────────────────
// 导出是给那些暂时不能整体换成 DataTable、但想对齐尺寸的地方用的。

export const CELL_FONT = 12;

const TABLE: CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: CELL_FONT,
};
const TH: CSSProperties = {
  textAlign: "left",
  padding: "4px 8px",
  borderBottom: "1px solid var(--border)",
  fontSize: 11,
  fontWeight: 600,
  color: "var(--text-muted)",
  // 不用 text-transform: uppercase —— 表头是中英混排, 大写只作用于拉丁字母,
  // 结果是「部门 请求 TOKENS 占比」里只有一个词在吼。
  letterSpacing: 0.2,
  whiteSpace: "nowrap",
};
const TD: CSSProperties = {
  padding: "4px 8px",
  borderBottom: "1px solid var(--border-soft)",
  verticalAlign: "top",
};
/** 数字列。tabular-nums 让 0-9 等宽, 一列数字的个/十/百位才对得齐。
 *  PerfPage 原来的 tdRightStyle 有这条, 抽组件时漏了 —— 是个退化。 */
const TD_NUM: CSSProperties = {
  ...TD,
  textAlign: "right",
  fontVariantNumeric: "tabular-nums",
  whiteSpace: "nowrap",
};

export interface Column<T> {
  /** 表头文字. 空字符串 = 操作列这种不需要标题的 */
  header: ReactNode;
  /** 单元格内容 */
  cell: (row: T, index: number) => ReactNode;
  /** 右对齐 —— 数字列该用, 不然一列数字对不齐没法扫读 */
  align?: "left" | "right";
  /** 固定列宽, 传数字按 px。不传则按内容分配 */
  width?: number | string;
  /** 不换行. 时间戳 / ID 这类断行反而更难读的内容用 */
  nowrap?: boolean;
  /** 超长省略号截断 (默认 220px, 可用 width 调).
   *
   * 密集表格里最伤的是"一格换 3 行, 整行跟着变 3 倍高" —— 行高节奏一乱,
   * 表格就退化成了列表。模型显示名这类不定长内容必须截, 完整值放 title。
   * 用的时候记得给单元格加 title, 不然截掉的部分就真没了。 */
  truncate?: boolean;
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  empty = "没数据",
  footer,
}: {
  columns: Array<Column<T>>;
  rows: readonly T[];
  rowKey: (row: T, index: number) => string;
  /** 整行可点 —— **只用于"触发一个动作"**, 比如设筛选。
   *
   * 如果点一行是**跳转到另一个页面**, 光有 onRowClick 不够: 用户会失去
   * 中键/⌘+点击开新标签、右键"在新标签页打开"、以及悬停看目标 URL。
   * 那种情况在某一格里放真 <Link> (通常是标题格), onRowClick 只作为
   * "点行内空白处也能进去"的便利。 */
  onRowClick?: (row: T) => void;
  empty?: ReactNode;
  /** 表格底部一行, 用于分页器 / 合计 */
  footer?: ReactNode;
}) {
  if (rows.length === 0) {
    return (
      <div style={{ fontSize: CELL_FONT, color: "var(--text-muted)", padding: "8px 0" }}>
        {empty}
      </div>
    );
  }
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={TABLE}>
        <thead>
          <tr>
            {columns.map((c, i) => (
              <th
                key={i}
                style={{
                  ...TH,
                  textAlign: c.align === "right" ? "right" : "left",
                  width: c.width,
                }}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr
              key={rowKey(r, ri)}
              onClick={onRowClick ? () => onRowClick(r) : undefined}
              // 整行可点就必须键盘也能点。<tr onClick> 本身 Tab 聚焦不到、
              // Enter/Space 也没反应 —— 那等于把这个功能对键盘用户整个关掉。
              // 导航类的行还应该在某一格里放真 <Link> (见下面 rowKey 的说明),
              // 这里的 role="button" 只覆盖"点一下触发一个动作"那类。
              tabIndex={onRowClick ? 0 : undefined}
              role={onRowClick ? "button" : undefined}
              onKeyDown={
                onRowClick
                  ? (e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onRowClick(r);
                      }
                    }
                  : undefined
              }
              style={onRowClick ? { cursor: "pointer" } : undefined}
              onMouseEnter={
                onRowClick
                  ? (e) => (e.currentTarget.style.background = "var(--bg-secondary)")
                  : undefined
              }
              onMouseLeave={
                onRowClick
                  ? (e) => (e.currentTarget.style.background = "transparent")
                  : undefined
              }
            >
              {columns.map((c, ci) => (
                <td
                  key={ci}
                  style={{
                    ...(c.align === "right" ? TD_NUM : TD),
                    whiteSpace: c.nowrap || c.truncate ? "nowrap" : undefined,
                    ...(c.truncate
                      ? {
                          maxWidth: c.width ?? 220,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                        }
                      : null),
                  }}
                >
                  {c.cell(r, ri)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {footer ? (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "6px 8px 0",
            fontSize: 11,
            color: "var(--text-muted)",
          }}
        >
          {footer}
        </div>
      ) : null}
    </div>
  );
}

/** 紧凑分区 —— 替 <Card>.
 *
 * Card 每张净损耗约 66px (padding 16×2 + h3 + marginBottom 12), 兄弟之间还有
 * 16px gap。一页 7 张 Card 光外壳就吃掉 460px, 而 800px 屏幕的可视区只有 715px。
 *
 * Card 本身不改 —— 它在 /market、Companion 那边也在用, 是"一张卡片"的语义。
 * 后台这种一屏要塞很多信息的场景另起一个更紧的。
 */
export function Section({
  title,
  action,
  children,
  style,
}: {
  title?: ReactNode;
  /** 标题右侧, 放刷新/新增这类按钮 */
  action?: ReactNode;
  children: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <div
      style={{
        background: "var(--bg-elev)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-md)",
        padding: 8,
        minWidth: 0,
        ...style,
      }}
    >
      {title || action ? (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            marginBottom: 4,
            minHeight: 18,
          }}
        >
          <h4 style={{ margin: 0, fontSize: 12, fontWeight: 600 }}>{title}</h4>
          <div style={{ flex: 1 }} />
          {action}
        </div>
      ) : null}
      {children}
    </div>
  );
}

export type BadgeTone = "neutral" | "ok" | "warn" | "err" | "accent";

const TONE: Record<BadgeTone, CSSProperties> = {
  // neutral 是描边不是填充 —— 一行里常有 2-3 个徽章, 全填充会盖过数据本身
  neutral: { border: "1px solid var(--border)", color: "var(--text-muted)" },
  ok: { background: "var(--status-ok)", color: "#fff" },
  warn: { background: "var(--status-warn)", color: "#fff" },
  err: { background: "var(--status-err)", color: "#fff" },
  accent: { background: "var(--accent)", color: "#fff" },
};

/** 状态徽章.
 *
 * 全仓有 6 份各写各的实现 (UsersPage 的 RoleBadge / Badge, FactsPage 的
 * StatusBadge, SystemPage 的 ActionTag, PerfPage 的 SourceBadge,
 * AdminPage 的 StatusBadge), 尺寸和配色互不相同。
 *
 * ⚠ **7/30 只换掉了 FactsPage 那一份。** 其余 5 份还在原地 —— 它们所在的
 * 页面这一轮没动, 单为换徽章去改 5 个文件不划算, 但也别把"打算统一"
 * 写成"已经统一了"。下次动到哪一页, 顺手换掉哪一份。
 *
 * 一个具体的坑: 那 5 份里有的没写 display:inline-block (例如 FactsPage
 * 换掉之前的那份)。inline 元素的纵向 padding **不撑行盒高度**, 在表格里
 * 背景色会溢出行分隔线。 */
export function Badge({
  tone = "neutral",
  children,
  title,
}: {
  tone?: BadgeTone;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      style={{
        display: "inline-block",
        fontSize: 10,
        lineHeight: 1.6,
        padding: "0 5px",
        borderRadius: 3,
        whiteSpace: "nowrap",
        ...TONE[tone],
      }}
    >
      {children}
    </span>
  );
}

export const BTN: CSSProperties = {
  padding: "2px 8px",
  border: "1px solid var(--border)",
  background: "transparent",
  color: "var(--text)",
  borderRadius: 3,
  cursor: "pointer",
  fontSize: 11,
  whiteSpace: "nowrap",
  fontFamily: "inherit",
};
export const BTN_PRIMARY: CSSProperties = {
  ...BTN,
  background: "var(--accent)",
  borderColor: "var(--accent)",
  color: "#fff",
};
export const BTN_DANGER: CSSProperties = {
  ...BTN,
  borderColor: "var(--status-err)",
  color: "var(--status-err)",
};

/** 一行工具条 —— 筛选控件放这里, 而不是各自套一张 Card.
 *
 * PerfPage / UsersPage 都有"整张 Card 只装一行筛选控件"的情况, 66px 外壳
 * 换 30px 内容。
 *
 * **左右分组是两个 flex 容器, 不是一个容器加 `<div style={{flex:1}}/>` 撑开。**
 * 后者在 flexWrap:wrap 下会出问题: spacer 的 flex-grow 只在**它自己那一行**
 * 生效, 于是窄屏换行时第一行变成"标题 + 一大片空白", 控件全掉到第二行。
 * 分成两组之后, 换行是右侧那组内部换, 左边的标题不会被孤零零顶在上面。
 */
export function Toolbar({
  title,
  children,
}: {
  /** 左侧标题. 用 h3 而不是加粗的 span —— Section 的标题是 h4,
   *  页面主标题得比它高一级, 否则整页没有任何 h1/h2/h3, 读屏软件拿不到页面标题。 */
  title?: ReactNode;
  /** 右侧控件 */
  children?: ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 8,
        fontSize: CELL_FONT,
      }}
    >
      {title ? (
        <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, whiteSpace: "nowrap" }}>
          {title}
        </h3>
      ) : (
        <span />
      )}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          flexWrap: "wrap",
          justifyContent: "flex-end",
          gap: 8,
          minWidth: 0,
        }}
      >
        {children}
      </div>
    </div>
  );
}
