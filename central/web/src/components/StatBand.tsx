/** 指标带 —— 页顶那一排大数 (8/1).
 *
 * ## 为什么抽出来
 *
 * 概览页和审计页显示的是**同一组五个数**（总请求 / 总 tokens / 活跃员工 /
 * 活跃部门 / 成本），来自同一个 `GlobalAudit`，在侧栏里只隔两格。它们各自
 * 写了一份：概览是 `Stat` + `Delta`，审计是 `SubStat` + `Trend`。
 *
 * 两份长得不一样是小事，**算得不一样是大事**：
 *
 *   概览页  总 tokens ↑12%  显示成橙色（`upIsGood={false}` —— 用量涨=多花钱）
 *   审计页  总 tokens ↑12%  显示成绿色（一律"涨即好"）
 *
 * 同一个数字、同一个涨幅，在两页上一个是警告一个是好消息。而这两页不会
 * 同时出现在一屏，所以没人会当场发现。
 *
 * 合并时取概览那套语义（涨好还是跌好由调用方说），因为"token 涨了是好事"
 * 这个默认在这个产品里是错的 —— 这里每一个 token 都是钱。
 *
 * 反过来，审计那套里有一件概览漏了的事：`prev === 0` 时概览直接 `return null`，
 * 于是一个**这期才出现**的部门跟"跟上期一样"长得完全一样。这里保留了「新增」。
 *
 * ## 为什么是横排 + 竖线，不是网格
 *
 * 原来两页都用等分网格（`1.6fr 1fr 1fr 1fr` / `auto-fit minmax(120px,1fr)`）。
 * 1400px 宽屏上五个指标被平均拉成每格 280px，数字贴在各自格子的左边缘，
 * 中间四大片空白 —— 看起来像没加载完。靠左排、用竖线分隔，边界是固定的，
 * 跟屏幕多宽无关。
 */

import type { ReactNode } from "react";

/** 一排指标的容器。直接塞 `<Stat>` 进去。 */
export function StatBand({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "stretch",
        gap: 0,
      }}
    >
      {children}
    </div>
  );
}

export function Stat({
  label,
  value,
  delta,
  hint,
  /** 主指标：字号大一档。一排里最多给一个，多给等于没给。 */
  hero,
}: {
  label: string;
  value: string | number;
  delta?: ReactNode;
  hint?: string;
  hero?: boolean;
}) {
  return (
    <div
      style={{
        // 竖线分隔而不是靠间距 —— 靠间距的话在宽屏上要么挤在一起
        // 要么散开, 分隔线让每个指标的边界固定。
        padding: "0 20px",
        borderRight: "1px solid var(--border-soft)",
        minWidth: 96,
      }}
    >
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 2 }}>
        {label}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 5 }}>
        <span
          style={{
            fontSize: hero ? 26 : 20,
            fontWeight: 600,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {value}
        </span>
        {delta}
      </div>
      {hint ? (
        <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 1 }}>
          {hint}
        </div>
      ) : null}
    </div>
  );
}

/** 跟上一个等长窗口的对比。
 *
 * `upIsGood` 没有默认值 —— 故意的。这个组件唯一容易搞错的地方就是它，
 * 给了默认值就会有人不填，而不填的那个方向恰恰是错的那个。
 */
export function Delta({
  now,
  prev,
  upIsGood,
}: {
  now: number;
  prev?: number | null;
  upIsGood: boolean;
}) {
  // 没有上期数据 = 没有参照系, 不是"没变化"。
  if (prev == null) return null;

  if (prev === 0) {
    // 上期 0、本期也 0：没什么可说的。
    if (now === 0) return null;
    // 上期 0、本期有量：算不出百分比, 但这恰恰是最值得看见的一种变化
    // —— 一个部门/模型这期才开始用。原来概览页在这里返 null,
    // 于是"新出现"和"没变化"在界面上是同一个样子。
    //
    // ⚠ 颜色同样要看 upIsGood。第一版这里写死了 status-warn, 于是
    // "活跃员工 0 → 5" 会显示成一个橙色警告 —— 而那是好消息。
    // 这正是这个文件存在的理由 (同一个变化在两页颜色不同), 差点在
    // 它自己身上又犯一次。
    return (
      <span
        style={{
          fontSize: 11,
          color: upIsGood ? "var(--text-muted)" : "var(--status-warn)",
        }}
        title="上一个等长窗口是 0 —— 这期才开始有量"
      >
        新增
      </span>
    );
  }

  const pct = ((now - prev) / prev) * 100;
  if (Math.abs(pct) < 0.5)
    return (
      <span
        style={{ fontSize: 11, color: "var(--text-muted)" }}
        title={`上期 ${prev.toLocaleString()}`}
      >
        持平
      </span>
    );

  const up = pct > 0;
  const good = up === upIsGood;
  return (
    <span
      style={{
        fontSize: 11,
        color: good ? "var(--text-muted)" : "var(--status-warn)",
      }}
      title={`上期 ${prev.toLocaleString()} · 跟上一个等长窗口对照`}
    >
      {up ? "↑" : "↓"}
      {Math.abs(pct).toFixed(0)}%
    </span>
  );
}
