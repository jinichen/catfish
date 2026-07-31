/** 后台页面的根容器 —— 一屏布局的那一层 (8/1).
 *
 * ## 为什么需要它
 *
 * 8/1 之前整个 `<main>` 是滚动容器: 翻到日志第 80 行时, 页面标题、筛选条、
 * 分页按钮全滚出屏幕了 —— 而这几样恰恰是这时候最需要在手边的
 * (改筛选要先滚回顶部, 翻页要先滚到底)。
 *
 * 改成"整页不动, 只有数据区滚"要求从 `<main>` 一路往下每一层都有确定高度。
 * 中间任何一层 `height: auto` 都会把高度交还给内容, 滚动条就又跑回外层 ——
 * **而且不报错**, 表现只是"这一屏没生效"。链条是:
 *
 *   App.tsx `<main>`  flex column + minHeight:0
 *   AdminLayout       flex:1 + minHeight:0, 内容列 flex column
 *   PageShell         ← 这里
 *   Section fill      flex:1, 内部滚
 *   DataTable fill    表身滚 + 表头 sticky
 *
 * ## scroll 是一句声明, 不是一个开关
 *
 *   scroll="page" (默认)  这一页整块滚。
 *   scroll="data"         滚动由里面某个 `<Section fill>` 接管。
 *
 * 两者的**样式是一样的** (都 `overflow: auto`) —— 第一版 data 模式用的是
 * `hidden`, 结果见下面 overflow 那段的说明: 一个页面有多条渲染路径,
 * fill 通常只在其中一条上, 走到别的分支时内容会直接消失。
 *
 * 所以 `scroll` 现在只表达意图, 由 scripts/check-page-scroll.mjs 拿它跟
 * 文件里实际有没有 fill 对账。
 */

import type { CSSProperties, ReactNode } from "react";

export function PageShell({
  children,
  // 改成 fail-safe 之后这个值不再影响样式 (见下面 overflow 那段), 它现在是
  // 一句**声明**: "这一页的滚动由里面某个 fill 的块接管"。
  // 留着不是摆设 —— scripts/check-page-scroll.mjs 拿它跟文件里实际有没有
  // fill 对账。声明和实现对不上正是这次要挡的那类错, 而对账需要有个东西
  // 写下声明。
  scroll: _scroll = "page",
  gap = 8,
  style,
}: {
  children: ReactNode;
  scroll?: "page" | "data";
  /** 块之间的间距。个别页面原来用的是 var(--space-4)=16, 保留可调。 */
  gap?: number | string;
  style?: CSSProperties;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap,
        flex: 1,
        // flex 子项默认 min-height:auto (= 不小于内容), 于是 overflow 永远
        // 不生效。这一条是整套布局里最容易漏的。
        minHeight: 0,
        // ⚠ 两种模式都是 auto, 不是 hidden。
        //
        // 第一版 data 模式写的是 `overflow: hidden` —— 逻辑上"这一块不该滚,
        // 里面的 fill 会接管"。但一个页面有**多条渲染路径**: 列表页点开
        // 编辑表单、报错、加载中、数据为空…… fill 通常只在其中一条上。
        // 走到别的分支时, 页面自己不滚、也没人接管, 内容直接被裁掉
        // **而且没有滚动条** —— 那是彻底看不见, 比整页滚糟得多。
        //
        // auto 的行为: 有 fill 子块时内容永远不会超出, 滚动条不出现,
        // 效果跟 hidden 一模一样; 漏了 fill 时退化成整页滚。
        // 也就是说失败方向是"没那么好看", 而不是"内容没了"。
        overflow: "auto",
        ...style,
      }}
    >
      {children}
    </div>
  );
}

/** 重新拉取期间, 把还显示着**上一批**数据的那块压暗并禁点 (8/1).
 *
 * 不这么做的话会有一段时间: 表里是上一次筛选的行, 而筛选条、选中高亮、
 * 分页号都已经是新的 —— 看起来像"查完了, 结果就是这些"。日志页原来就是
 * 这样: 改完部门点查询, 表里显示的还是上一次的行, 没有任何提示。
 *
 * 禁点是因为那时候点一行, 操作的是上一批数据里的那个实体。
 *
 * ⚠ 只包"显示数字的块"。工具栏、筛选条、分段切换要留在外面 —— 它们是
 * 改主意的出口, 请求卡住时把出口一起禁掉的话, 唯一能做的就是刷新整页。
 *
 * `fill` 在包着"要自己滚的那一块"时必须传: 这一层 height:auto 的话,
 * 高度确定的链条在这里断掉, 里面的 fill 撑不开, 滚动条会回到外层。
 */
export function Stale({
  loading,
  children,
  fill,
}: {
  loading: boolean;
  children: ReactNode;
  fill?: boolean;
}) {
  return (
    <div
      aria-busy={loading}
      style={{
        opacity: loading ? 0.55 : 1,
        pointerEvents: loading ? "none" : undefined,
        transition: "opacity 0.12s",
        ...(fill
          ? { flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }
          : null),
      }}
    >
      {children}
    </div>
  );
}
