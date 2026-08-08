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
  // 8/8: auto → fixed。
  //
  // 症状: /admin/providers 的「编辑 / 删除」两个按钮点不到 —— 它们没消失,
  // 是被挤到了滚动区右边看不见的地方 (实测表格 1169px 塞在 940px 的框里)。
  //
  // 病根在下面 truncate 那段: 它靠 `max-width` 截断, 而**百分比 max-width
  // 在 auto-layout 的 <td> 上浏览器直接忽略**。端点列写着 width:"30%" +
  // truncate, 于是那条 71 字符的 URL 一个字都没省略、把列撑到 493px,
  // 后面的操作列就被顶出了可视区。
  //
  // 这条 auto-layout 的坑 AuditBreakdowns.tsx:55 已经踩过一次, 当时的处理
  // 是"这一列改用 px" —— 治了一列, 没治病。fixed 布局下列宽只认表头声明,
  // 内容再长也撑不开列, truncate 才真正成立。
  //
  // ⚠ 换成 fixed 之后有两条硬约束 (下面 width 的注释里也写了):
  //   1. **每列都要给 width** —— 没给的列拿的是"剩余空间", 而已声明的 px
  //      加起来超过容器时剩余是负的, 那列会被算成 0 宽直接看不见。
  //   2. px 之和要能塞进目标面板 —— fixed 不会替你压缩纯 px 的列。
  tableLayout: "fixed",
};
const TH: CSSProperties = {
  textAlign: "left",
  padding: "4px 8px",
  // fixed 布局下 width 是**内容宽**, 左右各 8px padding 会额外加上去 ——
  // 声明 160 实得 176, 六列就白白多出 96px, 正好够把操作列顶出去。
  // border-box 让声明的数字就是最终列宽, 页面那边才算得准。
  boxSizing: "border-box",
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
  boxSizing: "border-box", // 同 TH: 声明的 width 就是最终列宽, 不再被 padding 撑大
  borderBottom: "1px solid var(--border-soft)",
  // 7/30 三改: top → middle。
  // 列基本都截断了 (truncate), 单元格不再换行, 这时 top 会让徽章和按钮
  // 吊在行的上边缘、跟同行的数字对不齐。真有一格换行时 middle 也比 top 好看。
  verticalAlign: "middle",
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
  /** 列宽。数字按 px, 也可以给 "26%"。
   *
   * ⚠ 表格是 `table-layout: fixed`, 所以这个值是**唯一**的列宽依据 ——
   * 内容多长都撑不开它。两条要守的:
   *
   *   1. **每列都写 width**, 最多留一列不写当弹性列。不写的列分的是
   *      "剩余空间", 已声明的加起来超过容器时剩余为负, 那列会被算成 **0 宽**
   *      整列消失 —— 比截断严重得多。
   *   2. **px 之和要塞得进面板** (后台内容区窄的时候约 940px)。fixed 不压缩
   *      纯 px 的列, 超了就整表横向滚动, 最右边那列 (通常是操作列) 看不见。
   *      塞不下就用百分比 —— 百分比是按表宽算的, 会跟着一起缩。
   */
  width?: number | string;
  /** 不换行. 时间戳 / ID 这类断行反而更难读的内容用 */
  nowrap?: boolean;
  /** 超长省略号截断.
   *
   * 密集表格里最伤的是"一格换 3 行, 整行跟着变 3 倍高" —— 行高节奏一乱,
   * 表格就退化成了列表。模型显示名这类不定长内容必须截, 完整值放 title。
   * 用的时候记得给单元格加 title, 不然截掉的部分就真没了。
   *
   * 8/8: 截断的边界现在是**列宽本身**, 不再是单元格上的 max-width。
   * 老写法 `max-width: c.width ?? 220` 有两个毛病: 百分比 max-width 在
   * auto-layout 的 <td> 上被浏览器忽略 (于是长 URL 照样撑开列, 正是
   * /admin/providers 那次的病根); 而没给 width 时那个 220 的默认值又会
   * 比实际列宽窄, 截出一段空白。fixed 布局下列宽是硬的, 直接靠它即可。 */
  truncate?: boolean;
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  rowActive,
  rowClickable,
  rowStyle,
  fill,
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
  /** 这一行是不是"当前选中"的 —— 审计页点一行下钻之后, 那一行要留着高亮,
   *  否则筛选生效之后完全看不出是从哪一行点进来的。
   *
   *  ⚠ 高亮必须在这里而不是让调用方自己往单元格里塞背景色: 悬停的
   *  onMouseLeave 会把整行背景抹回 transparent, 调用方设的高亮鼠标划过一次
   *  就没了 —— 而且只在划过之后才没, 所以很难当成 bug 认出来。 */
  rowActive?: (row: T) => boolean;
  /** 这一行能不能点。不传 = 都能点。
   *
   * 用途是"整张表可点, 但个别行的标识是空的, 点了会筛出个空条件"这种 ——
   * 那种行看起来跟别的一样可点, 点下去没反应, 用户只会以为界面卡了。
   * 与其让它假装能点, 不如把光标和键盘焦点一起摘掉。 */
  rowClickable?: (row: T) => boolean;
  /** 整行的额外样式 —— 表达"这一行整体处于某种状态" (已删 / 已停用)。
   *
   * ⚠ 必须在这一层, 不能让调用方往每个单元格里塞: 一是要在每列重复,
   * 二是行级的东西 (opacity / 背景) 在单元格上表现不一样。
   *
   * 8/1 补的: 用户页原来是 `<tr style={{opacity: deleted ? 0.5 : 1}}>`,
   * 换成 DataTable 之后没有等价物 —— 勾上"含已删"后, 已删行跟活跃行
   * 除了多一个徽章之外长得一模一样。 */
  rowStyle?: (row: T) => CSSProperties | undefined;
  empty?: ReactNode;
  /** 表格底部一行, 用于分页器 / 合计 */
  footer?: ReactNode;
  /** 一屏布局: 表身在自己这块里滚, **表头钉住, footer 钉住** (8/1).
   *
   * 表头 sticky 是这里的重点。200 行日志滚到第 80 行时, "342772" 这一格
   * 到底是 in 还是 out 就只能靠数了 —— 而这张表有 4 个连着的数字列。 */
  fill?: boolean;
}) {
  // 开发期护栏 (8/8)。fixed 布局把"没写 width"从"按内容分配"变成了
  // "分剩余空间", 剩余不够时那列直接 0 宽消失。两列以上不写 width 还会
  // 被均分 —— 标题列跟"大小"列一样宽。两种都是肉眼一看就不对但很容易
  // 一直没人报的样子, 所以在控制台先喊一声。
  if (import.meta.env.DEV) {
    const missing = columns.filter((c) => c.width == null).length;
    if (missing > 1) {
      // eslint-disable-next-line no-console
      console.warn(
        `[DataTable] ${missing} 列没写 width。表格是 table-layout:fixed, ` +
          `这些列会被**均分**剩余空间 (宽的窄的一样宽), 剩余不够时还会是 0 宽。` +
          `最多留一列不写。表头: ${columns.map((c) => (typeof c.header === "string" ? c.header || "(空)" : "…")).join(" / ")}`,
      );
    }
  }

  if (rows.length === 0) {
    // fill 时也要保持同一个外壳: 直接返裸 div 的话, 外面的 Section fill
    // 占满整屏而里面是个高度 auto 的小块 —— 一大片空框顶上一行灰字。
    // footer 照渲 —— "点一行 = 只看这个模型"这类提示在空表时也该在,
    // 否则用户不知道有数据时能点。
    return (
      <div
        style={
          fill ? { flex: 1, minHeight: 0, display: "flex", flexDirection: "column" } : undefined
        }
      >
        <div style={{ fontSize: CELL_FONT, color: "var(--text-muted)", padding: "8px 0" }}>
          {empty}
        </div>
        {footer ? (
          <div style={{ fontSize: 11, color: "var(--text-muted)", padding: "0 8px" }}>
            {footer}
          </div>
        ) : null}
      </div>
    );
  }
  return (
    <div
      style={
        fill
          ? { flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }
          : undefined
      }
    >
      <div
        style={
          fill
            ? { flex: 1, minHeight: 0, overflow: "auto" }
            : { overflowX: "auto" }
        }
      >
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
                    ...(fill
                      ? {
                          position: "sticky" as const,
                          top: 0,
                          // 必须给不透明背景 —— sticky 的表头只是浮在上面,
                          // 底下的行会**从它后面透出来**, 数字叠在表头文字上。
                          background: "var(--bg-elev)",
                          // 边框跟着 sticky 会被滚上来的内容盖掉 (border 属于
                          // 单元格盒子), 用 box-shadow 画那条线。
                          boxShadow: "inset 0 -1px 0 var(--border)",
                          borderBottom: "none",
                          zIndex: 1,
                        }
                      : null),
                  }}
                >
                  {c.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, ri) => {
              const clickable = !!onRowClick && (rowClickable?.(r) ?? true);
              return (
              <tr
                key={rowKey(r, ri)}
                onClick={clickable ? () => onRowClick(r) : undefined}
                // 整行可点就必须键盘也能点。<tr onClick> 本身 Tab 聚焦不到、
                // Enter/Space 也没反应 —— 那等于把这个功能对键盘用户整个关掉。
                // 导航类的行还应该在某一格里放真 <Link> (见下面 rowKey 的说明),
                // 这里的 role="button" 只覆盖"点一下触发一个动作"那类。
                tabIndex={clickable ? 0 : undefined}
                role={clickable ? "button" : undefined}
                // 选中只有一个底色的话, 读屏软件那边完全不存在。
                aria-pressed={clickable && rowActive ? rowActive(r) : undefined}
                onKeyDown={
                  clickable
                    ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          onRowClick!(r);
                        }
                      }
                    : undefined
                }
                style={{
                  ...(clickable ? { cursor: "pointer" } : null),
                  ...(rowActive?.(r) ? { background: "var(--row-active)" } : null),
                  ...rowStyle?.(r),
                }}
                onMouseEnter={
                  clickable
                    ? (e) => {
                        if (!rowActive?.(r))
                          e.currentTarget.style.background = "var(--bg-secondary)";
                      }
                    : undefined
                }
                onMouseLeave={
                  clickable
                    ? (e) => {
                        // 选中的行划过之后要回到高亮, 不是回到透明。
                        if (!rowActive?.(r)) e.currentTarget.style.background = "transparent";
                      }
                    : undefined
                }
              >
                {columns.map((c, ci) => (
                  <td
                    key={ci}
                    style={{
                      // ⚠ 展开顺序有讲究: 这里不能再无条件写一行
                      // `whiteSpace: c.nowrap ? "nowrap" : undefined` ——
                      // 那会把 TD_NUM 自带的 nowrap 覆盖成 undefined,
                      // 于是数字列的 "≈ ¥259" 会在空格处折成两行。
                      ...(c.align === "right" ? TD_NUM : TD),
                      ...(c.nowrap || c.truncate ? { whiteSpace: "nowrap" as const } : null),
                      ...(c.truncate
                        ? {
                            // maxWidth 去掉了 —— 见 Column.truncate 的说明:
                            // fixed 布局下列宽就是硬边界, 再叠一层 max-width
                            // 只会在"没给 width"时截得比列还窄, 留出空白。
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
              );
            })}
          </tbody>
        </table>
      </div>
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
  fill,
}: {
  title?: ReactNode;
  /** 标题右侧, 放刷新/新增这类按钮 */
  action?: ReactNode;
  children: ReactNode;
  style?: CSSProperties;
  /** 吃掉剩余高度, 内容超出时**在这一块里面**滚 (8/1).
   *
   * 后台是一屏布局: 整页不滚, 页面标题、筛选条、分页器一直在手边, 只有
   * 数据区滚。一页里最多标一个 —— 标两个的话剩余高度会被平分, 两块各滚各的,
   * 比整页滚更难用。
   *
   * ⚠ 只标这个不够: 从 <main> 到这里每一层都得有确定高度
   * (App.tsx 的 main / AdminLayout 的内容列 / 页面根 div)。中间任何一层
   * height:auto 都会把高度交还给内容, 于是滚动条又跑回外层 —— 而且不报错,
   * 看起来只是"这一屏没生效"。 */
  fill?: boolean;
}) {
  return (
    <div
      style={{
        background: "var(--bg-elev)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-md)",
        padding: 8,
        minWidth: 0,
        ...(fill
          ? {
              flex: 1,
              // 0 是"允许被压缩到多小", 120 是"再挤也得留这么多"。
              // 兄弟块 (工具栏 / 指标带 / 对账条) 都是 min-height:auto 不收缩,
              // 矮窗口 + 内容多时这一块会被挤成 0 高 —— 表格整块消失。
              // 给个地板, 挤不下就让 PageShell 那层滚。
              minHeight: 120,
              display: "flex",
              flexDirection: "column",
              // 标题那一行不跟着滚, 所以外壳自己不滚
              overflow: "hidden",
            }
          : null),
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
          {/* 页面主标题是 Toolbar 的 h1, 分区跟到 h2 —— h1 直接跳 h4 是断层。 */}
          <h2 style={{ margin: 0, fontSize: 12, fontWeight: 600 }}>{title}</h2>
          <div style={{ flex: 1 }} />
          {action}
        </div>
      ) : null}
      {fill ? (
        // 注意这里**不加 overflow** —— 滚动交给里面的 DataTable(fill),
        // 那样表头才能 sticky 在自己的滚动容器上。这里再套一层 overflow
        // 的话会出现两个滚动容器, 表头钉在外面那个上、纹丝不动地飘着。
        <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
          {children}
        </div>
      ) : (
        children
      )}
    </div>
  );
}

export type BadgeTone = "neutral" | "ok" | "warn" | "err" | "accent" | "accentSoft";

const TONE: Record<BadgeTone, CSSProperties> = {
  // neutral 是描边不是填充 —— 一行里常有 2-3 个徽章, 全填充会盖过数据本身
  neutral: { border: "1px solid var(--border)", color: "var(--text-muted)" },
  ok: { background: "var(--status-ok)", color: "#fff" },
  warn: { background: "var(--status-warn)", color: "#fff" },
  err: { background: "var(--status-err)", color: "#fff" },
  accent: { background: "var(--accent)", color: "#fff" },
  // 8/1: 跟 accent 同色但是描边。用来在同一族里再分一档 (用户页的
  // sysadmin 实心 / admin 描边) —— 加一个新颜色的话, 一列里色相太多,
  // 反而不如"同色深浅"扫得快。
  accentSoft: { border: "1px solid var(--accent)", color: "var(--accent)" },
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

/** 分段切换 —— 同一个问题的不同切面, 用它代替并排.
 *
 * ## 什么时候该用
 *
 * 几张表回答的是**同一个问题的不同切法**时 (例如"这些 token 花在哪":
 * 按部门 / 按模型 / 按员工)。并排的代价在窄内容区里很快就还不起:
 * 三张 4-5 列的表挤进 1450px, 每列摊到约 110px, 而模型显示名和邮箱都装不下,
 * 结果是**每个框内部各自横向滚动** —— 表头和数据错位, 左边的列被切掉半个字,
 * 那比多点一下糟得多。
 *
 * 代价是失去一眼横向对比。所以只在"同一问题的切面"上用; 如果两张表回答的是
 * 不同问题 (例如"用量"和"错误率"), 该并排还是要并排。
 *
 * ## 键盘
 *
 * 按 WAI-ARIA tabs 模式: tablist 里只有当前项进 Tab 序列, 左右方向键切换。
 * 一堆按钮各自可 Tab 的话, 键盘用户要按 N 次才能跨过这一组。
 */
export function Tabs<K extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: ReadonlyArray<{ key: K; label: ReactNode }>;
  active: K;
  onChange: (key: K) => void;
}) {
  const idx = tabs.findIndex((t) => t.key === active);
  return (
    <div role="tablist" style={{ display: "flex", gap: 2, flexWrap: "wrap" }}>
      {tabs.map((t) => {
        const on = t.key === active;
        return (
          <button
            key={t.key}
            role="tab"
            aria-selected={on}
            tabIndex={on ? 0 : -1}
            onClick={() => onChange(t.key)}
            onKeyDown={(e) => {
              if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
              e.preventDefault();
              const next = (idx + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
              onChange(tabs[next].key);
              // 焦点跟着走, 否则方向键切了内容但焦点还留在原来那个 tab 上
              const el = e.currentTarget.parentElement?.children[next];
              if (el instanceof HTMLElement) el.focus();
            }}
            style={{
              ...BTN,
              padding: "3px 12px",
              fontSize: 12,
              borderColor: on ? "var(--accent)" : "transparent",
              background: on ? "var(--accent)" : "transparent",
              color: on ? "#fff" : "var(--text-muted)",
              fontWeight: on ? 600 : 400,
            }}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}

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
  /** 左侧标题. 每页一个 Toolbar, 它就是页面主标题, 所以是 <h1>。
   *
   * 8/1 改的: 原来是 h3, 而 /admin 下**每一页**都用 Toolbar 当标题 ——
   * 于是整个后台一个 h1 都没有 (审计页那个 24px 的 h1 也在这次一并换掉了),
   * 读屏软件按标题跳转时拿不到"这是哪一页"。
   *
   * 字号仍是 13px。语义层级和视觉大小是两件事, 后台这种密集界面不需要
   * 一个 32px 的大标题来告诉你在哪 —— 侧栏已经高亮着了。 */
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
        <h1 style={{ margin: 0, fontSize: 13, fontWeight: 600, whiteSpace: "nowrap" }}>
          {title}
        </h1>
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
