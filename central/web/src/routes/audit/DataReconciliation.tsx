/** AuditPage DataReconciliation — 抽自 AuditPage.tsx (5/20 拆分).
 *
 * 数据对账模块, 显本地 vs PG 差异 (P0 备用诊断).
 */

import { Card } from "../../components/Card";
import type { GlobalAudit } from "../../lib/me";


function DataReconciliation({ audit }: { audit: GlobalAudit }) {
  const modelSum = audit.by_model.reduce((s, m) => s + m.count, 0);
  const deptSum = audit.by_department?.reduce((s, d) => s + d.count, 0) ?? 0;
  const userSum = audit.by_user.reduce((s, u) => s + u.count, 0);

  const modelOk = modelSum === audit.request_count;
  const deptOk = deptSum === audit.request_count;
  const userOk = userSum === audit.request_count;

  if (modelOk && deptOk && userOk) return null;

  const rows = [
    { label: "按模型加和", sum: modelSum, ok: modelOk },
    { label: "按部门加和", sum: deptSum, ok: deptOk },
    { label: "按员工加和", sum: userSum, ok: userOk },
  ];

  return (
    <Card title="⚠️ 数据对账 (部分分组加和 ≠ 总数)">
      <div style={{ fontSize: 12, lineHeight: 1.6 }}>
        总请求 <strong>{audit.request_count.toLocaleString()}</strong>, 但:
        <ul style={{ margin: "6px 0 6px 18px", padding: 0 }}>
          {rows.map((r) => (
            <li
              key={r.label}
              style={{
                color: r.ok ? "inherit" : "var(--status-warn)",
              }}
            >
              {r.label} = <strong>{r.sum.toLocaleString()}</strong>{" "}
              {r.ok
                ? "✓"
                : `✗ (差 ${(audit.request_count - r.sum).toLocaleString()})`}
            </li>
          ))}
        </ul>
        <div style={{ color: "var(--text-muted)", fontSize: 11 }}>
          典型原因: 老 audit 数据 user_email / department 缺 OIDC claim → 进 "(未分组)"
          桶 (本表已显式列出).
        </div>
      </div>
    </Card>
  );
}
export default DataReconciliation;
