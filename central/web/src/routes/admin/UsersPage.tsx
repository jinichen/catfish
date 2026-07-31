/** /admin/users — 完整用户管理 (BL-ARCH1 P1 5/10).
 *
 * sysadmin / admin 都能进, 但能力不同:
 *   - sysadmin: 列出含 sysadmin, 能创建 admin / sysadmin
 *   - admin: 屏蔽 sysadmin 行, 只能创建 employee / manager
 */

import { useEffect, useState } from "react";
import { PageShell } from "../../components/PageShell";
import { Link, Routes, Route, useNavigate, useParams } from "react-router-dom";

import { Card, Row } from "../../components/Card";
import {
  adminApi,
  type CreateUserReq,
  type Role,
  type UserBrief,
} from "../../lib/admin";
import { useAuthStore } from "../../store/auth";

export function UsersPage() {
  return (
    <Routes>
      <Route index element={<UsersList />} />
      <Route path="new" element={<UserCreate />} />
      <Route path=":email" element={<UserDetail />} />
      <Route path=":email/edit" element={<UserEdit />} />
    </Routes>
  );
}

// ── List ────────────────────────────────────────────────────────


function UsersList() {
  const me = useAuthStore((s) => s.me);
  const [users, setUsers] = useState<UserBrief[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [roleFilter, setRoleFilter] = useState<Role | "">("");
  const [includeDeleted, setIncludeDeleted] = useState(false);

  const refresh = async () => {
    setLoading(true);
    try {
      const r = await adminApi.listUsers({
        include_deleted: includeDeleted,
        role: roleFilter || undefined,
      });
      setUsers(r.users);
      setError(null);
    } catch (e) {
      // P3.5.80 (7/30 达华现场): 失败时**必须清空**, 不能留着上一次的结果。
      //
      // 老逻辑只 setError 不动 users:
      //   - 首次加载失败 → users 还是初始 []  → 标题写"0 个 user"
      //     而真实情况是"不知道有几个"。达华现场 identity 反代 502,
      //     页面理直气壮地报 0 个用户 —— 管理员会以为数据没了。
      //   - 刷新失败 → users 留着上一次的 50 条 → 标题写"50 个 user"
      //     旧数据冒充当前状态, 比报 0 更危险。
      //
      // 查不到就是查不到, 别给数字。
      setUsers([]);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roleFilter, includeDeleted]);

  const filtered = filter
    ? users.filter(
        (u) =>
          u.email.toLowerCase().includes(filter.toLowerCase()) ||
          u.name.toLowerCase().includes(filter.toLowerCase()) ||
          u.department.toLowerCase().includes(filter.toLowerCase()),
      )
    : users;

  return (
    <PageShell gap="var(--space-4)">
      <Card
        title={
          // 数量三态: 加载中 / 读取失败 / 真实数字。
          // 失败时不给数字 —— 理由见 refresh() 的 catch 分支。
          `用户管理 · ${
            error ? "读取失败" : loading ? "加载中…" : `${users.length} 个 user`
          } (${me?.role === "sysadmin" ? "sysadmin 视角全部" : "admin 视角不含 sysadmin"})`
        }
        action={
          <Link
            to="/admin/users/new"
            style={{
              background: "var(--accent)",
              color: "white",
              padding: "6px 14px",
              borderRadius: "var(--radius-sm)",
              fontSize: 13,
            }}
          >
            + 创建用户
          </Link>
        }
      >
        <div style={{ display: "flex", gap: "var(--space-3)", flexWrap: "wrap" }}>
          <input
            type="text"
            placeholder="搜 email / 名字 / 部门…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            style={{
              flex: 1,
              minWidth: 200,
              padding: "6px 10px",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm)",
              fontSize: 13,
            }}
          />
          <select
            value={roleFilter}
            onChange={(e) => setRoleFilter(e.target.value as Role | "")}
            style={{
              padding: "6px 10px",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm)",
              fontSize: 13,
            }}
          >
            <option value="">全部 role</option>
            {me?.role === "sysadmin" && <option value="sysadmin">sysadmin</option>}
            <option value="admin">admin</option>
            <option value="manager">manager</option>
            <option value="employee">employee</option>
          </select>
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              fontSize: 13,
              color: "var(--text-muted)",
            }}
          >
            <input
              type="checkbox"
              checked={includeDeleted}
              onChange={(e) => setIncludeDeleted(e.target.checked)}
            />
            含已删
          </label>
          <button
            onClick={() => void refresh()}
            style={{
              padding: "6px 12px",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm)",
              background: "var(--bg-secondary)",
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            刷新
          </button>
        </div>
      </Card>

      {loading && <div>加载中…</div>}
      {error && <div style={{ color: "var(--status-err)" }}>{error}</div>}

      {!loading && !error && (
        <Card title={`列表 (筛后 ${filtered.length})`}>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border)" }}>
                  <th style={{ textAlign: "left", padding: "6px 8px" }}>Email</th>
                  <th style={{ textAlign: "left", padding: "6px 8px" }}>名字</th>
                  <th style={{ textAlign: "left", padding: "6px 8px" }}>部门</th>
                  <th style={{ textAlign: "left", padding: "6px 8px" }}>Role</th>
                  <th style={{ textAlign: "left", padding: "6px 8px" }}>状态</th>
                  <th style={{ textAlign: "right", padding: "6px 8px" }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((u) => (
                  <UserRow key={u.email} user={u} onChange={refresh} />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </PageShell>
  );
}


function UserRow({ user, onChange }: { user: UserBrief; onChange: () => void }) {
  const [busy, setBusy] = useState(false);

  const handleLock = async () => {
    setBusy(true);
    try {
      await adminApi.lockUser(user.email, !user.locked);
      onChange();
    } catch (e) {
      alert(`锁/解锁失败: ${e}`);
    } finally {
      setBusy(false);
    }
  };

  const handleResetPwd = async () => {
    const newPwd = prompt(`给 ${user.email} 重置密码 (≥ 8 位):`);
    if (!newPwd || newPwd.length < 8) {
      if (newPwd) alert("密码至少 8 位");
      return;
    }
    setBusy(true);
    try {
      await adminApi.resetPassword(user.email, newPwd, true);
      alert(`已重置. 临时密码: ${newPwd}\n${user.email} 下次登录后必须改密.`);
    } catch (e) {
      alert(`重置失败: ${e}`);
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm(`确认删除 ${user.email}? (软删, 可恢复)`)) return;
    setBusy(true);
    try {
      await adminApi.deleteUser(user.email);
      onChange();
    } catch (e) {
      alert(`删除失败: ${e}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <tr style={{ borderBottom: "1px solid var(--bg-secondary)", opacity: user.deleted_at ? 0.5 : 1 }}>
      <td style={{ padding: "4px 8px" }}>
        <Link to={`/admin/users/${encodeURIComponent(user.email)}`}>{user.email}</Link>
      </td>
      <td style={{ padding: "4px 8px" }}>{user.name || "-"}</td>
      <td style={{ padding: "4px 8px", color: "var(--text-muted)" }}>{user.department || "-"}</td>
      <td style={{ padding: "4px 8px" }}>
        <RoleBadge role={user.role} />
      </td>
      <td style={{ padding: "4px 8px" }}>
        {user.deleted_at && <Badge color="err" label="已删" />}
        {user.locked && <Badge color="warn" label="已锁" />}
        {!user.deleted_at && !user.locked && <Badge color="ok" label="活跃" />}
        {user.must_change_password && <Badge color="warn" label="待改密" />}
      </td>
      <td style={{ padding: "4px 8px", textAlign: "right" }}>
        <div style={{ display: "inline-flex", gap: 4 }}>
          {!user.deleted_at && (
            <>
              <button
                onClick={handleLock}
                disabled={busy}
                style={btnSmall}
              >
                {user.locked ? "解锁" : "锁"}
              </button>
              <button onClick={handleResetPwd} disabled={busy} style={btnSmall}>
                重置密码
              </button>
              <Link
                to={`/admin/users/${encodeURIComponent(user.email)}/edit`}
                style={{ ...btnSmall, textDecoration: "none", color: "var(--text)" }}
              >
                编辑
              </Link>
              <button
                onClick={handleDelete}
                disabled={busy}
                style={{ ...btnSmall, color: "var(--status-err)" }}
              >
                删
              </button>
            </>
          )}
        </div>
      </td>
    </tr>
  );
}


const btnSmall: React.CSSProperties = {
  padding: "2px 8px",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  background: "var(--bg-secondary)",
  cursor: "pointer",
  fontSize: 12,
};

function RoleBadge({ role }: { role: Role }) {
  const colors: Record<Role, string> = {
    sysadmin: "#7c3aed",
    admin: "var(--accent)",
    manager: "var(--status-warn)",
    employee: "var(--text-muted)",
  };
  return (
    <span
      style={{
        background: colors[role] || "var(--text-muted)",
        color: "white",
        padding: "1px 8px",
        borderRadius: "var(--radius-sm)",
        fontSize: 11,
      }}
    >
      {role}
    </span>
  );
}

function Badge({ color, label }: { color: "ok" | "warn" | "err"; label: string }) {
  const colors = {
    ok: "var(--status-ok)",
    warn: "var(--status-warn)",
    err: "var(--status-err)",
  };
  return (
    <span
      style={{
        background: colors[color],
        color: "white",
        padding: "1px 6px",
        borderRadius: "var(--radius-sm)",
        fontSize: 10,
        marginRight: 4,
      }}
    >
      {label}
    </span>
  );
}

// ── Create ──────────────────────────────────────────────────────


function UserCreate() {
  const navigate = useNavigate();
  const me = useAuthStore((s) => s.me);
  const [form, setForm] = useState<CreateUserReq>({
    email: "",
    password: "",
    name: "",
    department: "",
    role: "employee",
    managed_departments: [],
    must_change_password: true,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isSysadmin = me?.role === "sysadmin";

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.email || !form.password) {
      setError("email + 密码必填");
      return;
    }
    if (form.password.length < 8) {
      setError("密码至少 8 位");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await adminApi.createUser(form);
      alert(`创建成功. 临时密码: ${form.password}\n员工首次登录后必须改密.`);
      navigate("/admin/users");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="创建用户">
      <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
        <Field label="Email *">
          <input
            type="email"
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
            placeholder="zhangsan@ffcs.cn"
            style={inputStyle}
            required
          />
        </Field>
        <Field label="临时密码 * (≥ 8 位, 员工首次登录强制改)">
          <input
            type="text"
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            placeholder="员工首次登录会被强制改"
            style={inputStyle}
            required
          />
          <button
            type="button"
            onClick={() => setForm({ ...form, password: genTempPassword() })}
            style={{ ...btnSmall, marginTop: 4 }}
          >
            生成强密码
          </button>
        </Field>
        <Field label="姓名">
          <input
            type="text"
            value={form.name || ""}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            style={inputStyle}
          />
        </Field>
        <Field label="部门">
          <input
            type="text"
            value={form.department || ""}
            onChange={(e) => setForm({ ...form, department: e.target.value })}
            placeholder="engineering / 研发部 / 销售部 ..."
            style={inputStyle}
          />
        </Field>
        <Field label="Role">
          <select
            value={form.role}
            onChange={(e) => setForm({ ...form, role: e.target.value as Role })}
            style={inputStyle}
          >
            <option value="employee">employee — 普通员工</option>
            <option value="manager">manager — 部门经理</option>
            {isSysadmin && (
              <>
                <option value="admin">admin — 公司管理员 (sysadmin only)</option>
                <option value="sysadmin">sysadmin — 系统超级管理员 (sysadmin only)</option>
              </>
            )}
          </select>
          {!isSysadmin && (
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
              admin / sysadmin 角色只 sysadmin 能创建.
            </div>
          )}
        </Field>
        {form.role === "manager" && (
          <Field label="管的部门 (manager 才有, 多个用逗号)">
            <input
              type="text"
              value={(form.managed_departments || []).join(", ")}
              onChange={(e) =>
                setForm({
                  ...form,
                  managed_departments: e.target.value.split(/[,，]\s*/).filter(Boolean),
                })
              }
              placeholder="研发部, 产品部"
              style={inputStyle}
            />
          </Field>
        )}

        {error && (
          <div style={{ color: "var(--status-err)", fontSize: 13 }}>{error}</div>
        )}

        <div style={{ display: "flex", gap: "var(--space-2)" }}>
          <button
            type="submit"
            disabled={busy}
            style={{
              background: "var(--accent)",
              color: "white",
              border: "none",
              padding: "6px 16px",
              borderRadius: "var(--radius-sm)",
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            {busy ? "创建中…" : "创建"}
          </button>
          <button
            type="button"
            onClick={() => navigate("/admin/users")}
            style={{
              background: "transparent",
              border: "1px solid var(--border)",
              padding: "6px 16px",
              borderRadius: "var(--radius-sm)",
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            取消
          </button>
        </div>
      </form>
    </Card>
  );
}


function genTempPassword(): string {
  const chars = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789!@#$%";
  return Array.from({ length: 12 }, () =>
    chars.charAt(Math.floor(Math.random() * chars.length)),
  ).join("");
}


function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label>
      <div style={{ marginBottom: 4, fontSize: 13 }}>{label}</div>
      {children}
    </label>
  );
}


const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "6px 10px",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  fontSize: 13,
  boxSizing: "border-box",
};


// ── Detail ──────────────────────────────────────────────────────


function UserDetail() {
  const { email } = useParams<{ email: string }>();
  const [user, setUser] = useState<UserBrief | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!email) return;
    adminApi
      .getUser(email)
      .then(setUser)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [email]);

  if (!email) return <div>路径错</div>;
  if (error) return <div style={{ color: "var(--status-err)" }}>{error}</div>;
  if (!user) return <div>加载中…</div>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <Card
        title={user.email}
        action={
          <Link
            to={`/admin/users/${encodeURIComponent(user.email)}/edit`}
            style={{
              background: "var(--accent)",
              color: "white",
              padding: "4px 12px",
              borderRadius: "var(--radius-sm)",
              fontSize: 12,
            }}
          >
            编辑
          </Link>
        }
      >
        <Row label="姓名" value={user.name || "-"} />
        <Row label="部门" value={user.department || "-"} />
        <Row label="Role" value={<RoleBadge role={user.role} />} />
        {user.managed_departments.length > 0 && (
          <Row label="管的部门" value={user.managed_departments.join(", ")} />
        )}
        <Row
          label="状态"
          value={
            <>
              {user.deleted_at && <Badge color="err" label="已删" />}
              {user.locked && <Badge color="warn" label="已锁" />}
              {!user.deleted_at && !user.locked && <Badge color="ok" label="活跃" />}
              {user.must_change_password && <Badge color="warn" label="待改密" />}
            </>
          }
        />
        <Row label="创建于" value={user.created_at?.slice(0, 19) || "-"} mono />
        <Row label="最后登录" value={user.last_login_at?.slice(0, 19) || "从未"} mono />
        {user.locked_at && (
          <Row label="锁定时间" value={user.locked_at.slice(0, 19)} mono />
        )}
      </Card>
    </div>
  );
}


// ── Edit ────────────────────────────────────────────────────────


function UserEdit() {
  const { email } = useParams<{ email: string }>();
  const navigate = useNavigate();
  const me = useAuthStore((s) => s.me);
  const [user, setUser] = useState<UserBrief | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isSysadmin = me?.role === "sysadmin";

  useEffect(() => {
    if (!email) return;
    adminApi.getUser(email).then(setUser).catch((e) => setError(String(e)));
  }, [email]);

  if (!email) return <div>路径错</div>;
  if (error) return <div style={{ color: "var(--status-err)" }}>{error}</div>;
  if (!user) return <div>加载中…</div>;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await adminApi.updateUser(user.email, {
        name: user.name,
        department: user.department,
        role: user.role,
        managed_departments: user.managed_departments,
      });
      navigate(`/admin/users/${encodeURIComponent(user.email)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={`编辑 ${user.email}`}>
      <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
        <Field label="姓名">
          <input
            type="text"
            value={user.name}
            onChange={(e) => setUser({ ...user, name: e.target.value })}
            style={inputStyle}
          />
        </Field>
        <Field label="部门">
          <input
            type="text"
            value={user.department}
            onChange={(e) => setUser({ ...user, department: e.target.value })}
            style={inputStyle}
          />
        </Field>
        <Field label="Role">
          <select
            value={user.role}
            onChange={(e) => setUser({ ...user, role: e.target.value as Role })}
            style={inputStyle}
          >
            <option value="employee">employee</option>
            <option value="manager">manager</option>
            {isSysadmin && (
              <>
                <option value="admin">admin</option>
                <option value="sysadmin">sysadmin</option>
              </>
            )}
          </select>
        </Field>
        {user.role === "manager" && (
          <Field label="管的部门">
            <input
              type="text"
              value={user.managed_departments.join(", ")}
              onChange={(e) =>
                setUser({
                  ...user,
                  managed_departments: e.target.value.split(/[,，]\s*/).filter(Boolean),
                })
              }
              style={inputStyle}
            />
          </Field>
        )}
        {error && <div style={{ color: "var(--status-err)" }}>{error}</div>}
        <div style={{ display: "flex", gap: "var(--space-2)" }}>
          <button
            type="submit"
            disabled={busy}
            style={{
              background: "var(--accent)",
              color: "white",
              border: "none",
              padding: "6px 16px",
              borderRadius: "var(--radius-sm)",
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            {busy ? "保存中…" : "保存"}
          </button>
          <button
            type="button"
            onClick={() => navigate(`/admin/users/${encodeURIComponent(user.email)}`)}
            style={{
              background: "transparent",
              border: "1px solid var(--border)",
              padding: "6px 16px",
              borderRadius: "var(--radius-sm)",
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            取消
          </button>
        </div>
      </form>
    </Card>
  );
}
