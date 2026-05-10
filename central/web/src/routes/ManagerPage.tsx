/** /manager — 部门视图 (manager + admin only) (BL-ARCH1 5/10).
 *
 * 列我管的部门, 点进去看本部门 quota / audit / top 员工.
 * Manager 看自己的 managed_departments, admin 看所有部门.
 */

import { useEffect, useState } from "react";
import { useParams, Link, Routes, Route, useNavigate } from "react-router-dom";

import { Card, Row } from "../components/Card";
import { RoleGate } from "../components/RoleGate";
import {
  fetchDepartmentAudit,
  fetchDepartmentQuota,
  updateDepartmentQuota,
  type DepartmentAudit,
  type DepartmentQuota,
} from "../lib/me";
import { useAuthStore } from "../store/auth";

export function ManagerPage() {
  return (
    <RoleGate require={["manager", "admin"]}>
      <Routes>
        <Route index element={<DepartmentList />} />
        <Route path=":dept" element={<DepartmentDetail />} />
      </Routes>
    </RoleGate>
  );
}

function DepartmentList() {
  const me = useAuthStore((s) => s.me);
  const navigate = useNavigate();
  if (!me) return null;

  const depts =
    me.role === "admin" ? me.managed_departments.length > 0
      ? me.managed_departments
      : ["engineering", "product", "sales", "finance"]  // admin 默认列常见部门
    : me.managed_departments;

  if (depts.length === 0) {
    return (
      <Card title="没有可管的部门">
        <div style={{ color: "var(--text-muted)" }}>
          你是 {me.role}, 但没设 managed_departments. 找 admin 给你授权.
        </div>
      </Card>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <Card title="选部门">
        <div style={{ color: "var(--text-muted)", fontSize: 13, marginBottom: "var(--space-3)" }}>
          你管以下部门. 点击查看本部门今日 quota / audit / top 员工:
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
            gap: "var(--space-2)",
          }}
        >
          {depts.map((d) => (
            <button
              key={d}
              onClick={() => navigate(`/manager/${encodeURIComponent(d)}`)}
              style={{
                background: "var(--bg-elev)",
                border: "1px solid var(--border)",
                padding: "var(--space-3)",
                borderRadius: "var(--radius-sm)",
                cursor: "pointer",
                textAlign: "left",
              }}
            >
              <div style={{ fontWeight: 500 }}>{d}</div>
            </button>
          ))}
        </div>
      </Card>
    </div>
  );
}

function fmtTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}K`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

function DepartmentDetail() {
  const { dept } = useParams<{ dept: string }>();
  const [quota, setQuota] = useState<DepartmentQuota | null>(null);
  const [audit, setAudit] = useState<DepartmentAudit | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editLimit, setEditLimit] = useState<string>("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!dept) return;
    Promise.all([fetchDepartmentQuota(dept), fetchDepartmentAudit(dept)])
      .then(([q, a]) => {
        setQuota(q);
        setAudit(a);
        setEditLimit(String(q.day.limit));
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [dept]);

  if (!dept) return <div>路径错</div>;
  if (error) return <div style={{ color: "var(--status-err)" }}>{error}</div>;
  if (!quota || !audit) return <div>加载中…</div>;

  const handleUpdateQuota = async () => {
    const n = Number(editLimit);
    if (!Number.isFinite(n) || n < 0) {
      alert("limit 必须是非负数 (0 = 不限)");
      return;
    }
    setBusy(true);
    const r = await updateDepartmentQuota(dept, n);
    setBusy(false);
    if (!r.ok) {
      alert(`改失败: ${r.detail}`);
    } else {
      alert(`已改 ${dept} 部门 day limit = ${n}`);
      // refresh
      fetchDepartmentQuota(dept).then(setQuota);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <div>
        <Link to="/manager" style={{ fontSize: 13 }}>
          ← 选别的部门
        </Link>
      </div>

      <Card title={`部门: ${dept}`}>
        <Row
          label="今日已用"
          value={`${fmtTokens(quota.day.used)} tok`}
        />
        <Row
          label="日限额"
          value={
            <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
              <input
                type="number"
                value={editLimit}
                onChange={(e) => setEditLimit(e.target.value)}
                style={{
                  width: 120,
                  padding: "2px 6px",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-sm)",
                }}
              />
              <button
                onClick={handleUpdateQuota}
                disabled={busy}
                style={{
                  background: "var(--accent)",
                  color: "white",
                  border: "none",
                  padding: "4px 10px",
                  borderRadius: "var(--radius-sm)",
                  fontSize: 12,
                  cursor: "pointer",
                }}
              >
                {busy ? "..." : "改"}
              </button>
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                (0 = 不限)
              </span>
            </div>
          }
        />
      </Card>

      <Card title="本部门今日 top 员工 (按用量)">
        {quota.top_users.length === 0 ? (
          <div style={{ color: "var(--text-muted)" }}>没数据</div>
        ) : (
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            {quota.top_users.map((u) => (
              <li key={u.user_email}>
                {u.user_email} · {fmtTokens(u.tokens_used)} tok
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title={`本部门最近 audit (${audit.request_count} 请求, ${fmtTokens(audit.total_tokens)} tok)`}>
        <div style={{ marginBottom: "var(--space-3)" }}>
          <div
            style={{
              fontSize: 12,
              color: "var(--text-muted)",
              marginBottom: 4,
            }}
          >
            按模型分布:
          </div>
          {audit.by_model.map((m) => (
            <Row
              key={m.model}
              label={m.model}
              value={`${m.count} 次 · ${fmtTokens(m.total_tokens)} tok`}
            />
          ))}
        </div>
        <div>
          <div
            style={{
              fontSize: 12,
              color: "var(--text-muted)",
              marginBottom: 4,
            }}
          >
            按员工分布:
          </div>
          {audit.by_user.slice(0, 10).map((u) => (
            <Row
              key={u.user_email}
              label={u.user_email}
              value={`${u.count} 次 · ${fmtTokens(u.total_tokens)} tok`}
            />
          ))}
        </div>
      </Card>
    </div>
  );
}
