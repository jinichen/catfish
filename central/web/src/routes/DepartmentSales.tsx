/** 部门销售漏斗 (销售跟进阶段 3)。
 *
 * 每个销售在 Companion「🤝 销售跟进 → 发布给主管」主动发的摘要, 这里按部门合起来:
 * 全组按阶段的客户数 / 预计金额, 每个人的发布时间, 以及要优先盯的客户 (有跟进提醒的排前面)。
 * 没发布的人不会出现 —— 这里只显示员工主动交出来的数据。
 */
import { useEffect, useState } from "react";

import { DataTable } from "../components/DataTable";
import { Stat, StatBand } from "../components/StatBand";
import { fetchDepartmentSales, type DepartmentSales, type SalesCustomerRow } from "../lib/sales";

const money = (n: number) => (n >= 10000 ? `${(n / 10000).toFixed(n % 10000 === 0 ? 0 : 1)}万` : n.toLocaleString());
const day = (iso: string) => (iso || "").replace("T", " ").slice(0, 16);

type Row = SalesCustomerRow & { member: string };

export function DepartmentSalesView({ dept }: { dept: string }) {
  const [data, setData] = useState<DepartmentSales | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    fetchDepartmentSales(dept)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [dept]);

  if (error) return <div style={{ fontSize: 12, color: "var(--status-err)" }}>{error}</div>;
  if (!data) return <div style={{ fontSize: 12, color: "var(--text-muted)" }}>加载中…</div>;
  if (data.members.length === 0)
    return (
      <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
        本部门还没有人发布销售漏斗。销售在鲶鱼 Companion「设置 → 🤝 销售跟进 → 发布给主管」点一下就会出现在这里。
      </div>
    );

  const rows: Row[] = data.members.flatMap((m) => m.customers.map((c) => ({ ...c, member: m.owner || m.published_by })));
  rows.sort((a, b) => b.flags.length - a.flags.length || (b.deal_value ?? 0) - (a.deal_value ?? 0));
  const total = data.funnel.reduce((s, f) => s + f.count, 0);
  const amount = data.funnel.reduce((s, f) => s + f.deal_total, 0);
  const flagged = rows.filter((r) => r.flags.length > 0).length;

  return (
    <>
      <StatBand>
        <Stat hero label="客户" value={total} />
        <Stat label="预计金额合计" value={money(amount)} />
        <Stat label="要跟进" value={flagged} hint="有提醒的客户" />
        <Stat label="已发布销售" value={data.members.length} />
      </StatBand>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", margin: "8px 0" }}>
        {data.funnel.map((f) => (
          <span key={f.stage} style={{ fontSize: 12, padding: "3px 8px", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)" }}>
            {f.stage} <b>{f.count}</b>
            {f.deal_total ? ` · ${money(f.deal_total)}` : ""}
          </span>
        ))}
      </div>
      <DataTable
        rows={rows}
        rowKey={(r) => `${r.member}/${r.name}`}
        columns={[
          { header: "客户", cell: (r) => r.name, width: "16%", truncate: true },
          { header: "销售", cell: (r) => r.member, width: "10%", truncate: true },
          { header: "阶段", cell: (r) => r.stage, width: "9%" },
          { header: "预计金额", cell: (r) => r.deal_size, width: "10%", truncate: true },
          { header: "最近联系", cell: (r) => r.last_contact, width: "11%", nowrap: true },
          { header: "下一步", cell: (r) => `${r.next_action}${r.next_action_due ? ` (${r.next_action_due})` : ""}`, truncate: true },
          {
            header: "提醒",
            cell: (r) => <span style={{ color: r.flags.length ? "var(--status-warn)" : undefined }}>{r.flags.join("、")}</span>,
            width: "18%",
            truncate: true,
          },
        ]}
      />
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6 }}>
        发布时间: {data.members.map((m) => `${m.owner || m.published_by} ${day(m.updated_at)}`).join(" · ")}
        。数据由销售主动发布, 不含聊天原文; 要最新的请让销售在 Companion 里点「更新发布」。
      </div>
    </>
  );
}
