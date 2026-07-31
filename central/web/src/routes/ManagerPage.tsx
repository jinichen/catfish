/** /admin/departments — 部门视图 (manager+) (BL-ARCH1 5/10, 7/30 从 /manager 搬来).
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
import { adminApi } from "../lib/admin";
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
  // admin+ 没设 managed_departments 时, 从服务端取真实部门列表 (见下面 useEffect).
  const [allDepts, setAllDepts] = useState<string[] | null>(null);
  const [listErr, setListErr] = useState<string | null>(null);

  const isAdminOrAbove = me?.role === "admin" || me?.role === "sysadmin";
  const needsAll = isAdminOrAbove && me.managed_departments.length === 0;

  // 7/30: 这里原来是写死的 ["engineering", "product", "sales", "finance"] ——
  // 那是 alembic 20260517_004 seed 的 4 个开发用部门。客户现场的部门叫什么
  // 我们不可能猜中 (达华就不是这四个), 而猜错的表现是**列出四个不存在的部门,
  // 点进去每个都空**, 不报错。
  //
  // 服务端本来就有 GET /api/admin/departments (P3.5.95 补的透传)。
  // 注意它是 require_admin_or_above —— manager 调会 403, 所以只有 admin+ 走这条;
  // manager 本来也只该看自己 managed_departments。
  useEffect(() => {
    if (!needsAll) return;
    let alive = true;
    adminApi
      .listDepartments()
      .then((r) => alive && setAllDepts(r.departments.map((d) => d.name)))
      .catch((e) => alive && setListErr(String(e)));
    return () => {
      alive = false;
    };
  }, [needsAll]);

  if (!me) return null;

  const depts = me.managed_departments.length > 0 ? me.managed_departments : (allDepts ?? []);

  if (needsAll && allDepts === null && !listErr) {
    return <Card title="部门"><div style={{ color: "var(--text-muted)" }}>加载中…</div></Card>;
  }

  if (depts.length === 0) {
    // 7/30: 老文案是"你是 {role}, 但没设 managed_departments. 找 admin 给你授权."
    // 对 sysadmin 说这话是荒唐的 —— 他就是最高权限, 没有"更上面的 admin"可找。
    // 而且它没说去哪儿设。授权入口是 /admin/users → 选人 → managed_departments。
    return (
      <Card title="没有可管的部门">
        <div style={{ color: "var(--text-muted)", lineHeight: 1.7 }}>
          {listErr ? (
            <>读部门列表失败: <code>{listErr}</code></>
          ) : isAdminOrAbove ? (
            <>
              系统里还没有任何部门, 或者你的账号没设 <code>managed_departments</code>。
              <br />
              到 <Link to="/admin/users">用户管理</Link> 选中账号, 在
              <code> managed_departments </code>里填部门名即可。
            </>
          ) : (
            <>
              你的账号还没被授权管理任何部门。找管理员在
              <b> 用户管理 → 你的账号 → managed_departments </b>里加上部门名。
            </>
          )}
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
              onClick={() => navigate(`/admin/departments/${encodeURIComponent(d)}`)}
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
        <Link to="/admin/departments" style={{ fontSize: 13 }}>
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
