/** 市场几页共用的小件 (11/09 跟控制台统一样式时抽的)。
 *
 * 版式跟控制台一样: Toolbar (页标题 + 右侧按钮) → Section (分段切换 + 搜索 + 一行说明)
 * → Section fill (表格, 只有它滚)。这里只放市场特有的: 说明行、搜索框、分段标签、状态徽章、时间。
 */
import { createContext, useContext, type CSSProperties, type ReactNode } from "react";

import { Link } from "react-router-dom";

import { Badge, Section, Tabs, Toolbar } from "../components/DataTable";
import { PageShell } from "../components/PageShell";

/** 跟控制台筛选条的输入框同尺寸 (admin/UsersPage INPUT) */
export const INPUT: CSSProperties = {
  background: "var(--bg-elev)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  padding: "3px 8px",
  color: "var(--text)",
  fontSize: 12,
  fontFamily: "inherit",
};

export const MUTED: CSSProperties = { fontSize: 12, color: "var(--text-muted)" };

/** 审核完一条后页面调它, 市场导航上的待审数跟着刷新 (MarketPage 提供; 不在市场外壳里时是空操作) */
export const RefreshCounts = createContext<() => void>(() => {});
export const useRefreshMarketCounts = () => useContext(RefreshCounts);

export const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** 「已上架 (3)」—— 数字跟控制台「按部门 2」一样跟在后面; 还没加载完不带数字 */
export const tabLabel = (label: string, n?: number | null) => (n == null ? label : `${label} ${n}`);

/** 审核状态 → 徽章颜色 (技能 / 连接器 / 专家包同一套) */
export function StatusBadge({ status, label }: { status?: string | null; label: ReactNode }) {
  const tone = status === "approved" ? "ok" : status === "rejected" ? "err" : status === "pending" ? "warn" : "neutral";
  return <Badge tone={tone}>{label}</Badge>;
}

/** 中央存的是 UTC (…+00:00), 显示成本地「11/09 10:23」; 一周前的只给日期 */
export function when(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  const p = (n: number) => String(n).padStart(2, "0");
  if (Date.now() - d.getTime() > 7 * 86400_000) return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  return `${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 筛选条: 分段切换 (已上架 / 待我审核 / 我提交的) + 搜索 + 一行说明。
 *  留在滚动区外面 —— 翻到第 40 行才想起要换分段时, 不该先滚回顶部。 */
export function FilterBar<K extends string>({
  tabs,
  tab,
  onTab,
  q,
  onQ,
  placeholder,
  hint,
  extra,
}: {
  tabs?: ReadonlyArray<{ key: K; label: ReactNode }>;
  tab?: K;
  onTab?: (k: K) => void;
  q?: string;
  onQ?: (v: string) => void;
  placeholder?: string;
  /** 一行灰字: 这一段是什么、在哪里装 */
  hint?: ReactNode;
  extra?: ReactNode;
}) {
  return (
    <Section>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        {tabs && tab && onTab && tabs.length > 1 && <Tabs tabs={tabs} active={tab} onChange={onTab} />}
        {onQ && (
          <input
            type="search"
            placeholder={placeholder}
            value={q}
            onChange={(e) => onQ(e.target.value)}
            style={{ ...INPUT, flex: 1, minWidth: 200 }}
          />
        )}
        {extra}
      </div>
      {hint && <div style={{ ...MUTED, marginTop: 6, lineHeight: 1.6 }}>{hint}</div>}
    </Section>
  );
}

/** 表格里「名称 + 下面一行灰色说明」的那一格 (说明截一行, 全文在 title) */
export function NameCell({ name, sub, mono }: { name: ReactNode; sub?: string | null; mono?: boolean }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontWeight: 500, fontFamily: mono ? "var(--font-mono)" : undefined, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {name}
      </div>
      {sub && (
        <div title={sub} style={{ ...MUTED, fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {sub}
        </div>
      )}
    </div>
  );
}

/** 详情 / 表单页的外壳: 标题是「连接器 / 提交」这样的面包屑, 前半截点回列表; 下面的卡片照旧 */
export function SubPage({ back, section, title, children }: { back: string; section: string; title: ReactNode; children: ReactNode }) {
  return (
    <PageShell gap="var(--space-4)">
      <Toolbar
        title={
          <>
            <Link to={back} style={{ color: "var(--text-muted)", fontWeight: 400 }}>{section}</Link> / {title}
          </>
        }
      />
      {children}
    </PageShell>
  );
}
