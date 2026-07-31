/** /admin/users 的三个表单页 — 创建 / 详情 / 编辑 (8/1 从 UsersPage 拆出).
 *
 * 拆的原因是 UsersPage 到了 743 行, 而这次要给它加"重置密码"对话框
 * (原来是 window.prompt) —— 加完会撞 CLAUDE.md 军规 §1 的 800 行红线。
 *
 * 拆的切口是"列表页 vs 表单页": 前者是一屏布局 + 密集表格, 后者是竖排
 * 表单, 两边的关注点本来就不一样。共用的只有 adminApi 和 RoleBadge。
 *
 * ⚠ 这三个页面**没有跟着这一轮改密度**: 它们还是 Card + Row + 13px 表单。
 * 表单页跟列表页的取舍不同 (表单要的是好填, 不是塞得下), 硬套密集表格的
 * 尺寸反而更难用。要改的话是单独一轮, 别混在布局改造里。
 */

import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { Card, Row } from "../../components/Card";
import { Badge } from "../../components/DataTable";
import {
  adminApi,
  type CreateUserReq,
  type Role,
  type UserBrief,
} from "../../lib/admin";
import { useAuthStore } from "../../store/auth";
import { RoleBadge, btnSmall, genTempPassword } from "./usersShared";


export function UserCreate() {
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
  const [done, setDone] = useState(false);

  // ⚠ 必须 clearTimeout。react-router 的 navigate 在组件卸载后**不会**短路
  // (activeRef 只在 layout effect 里设 true, 没有 cleanup 设回 false),
  // React 18 也早就不打"卸载后 setState"的警告了 —— 所以裸 setTimeout 的
  // 表现是: 保存成功后一秒内点侧栏去别的页, 到点被硬拽回来, 控制台干净。
  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => navigate("/admin/users"), 900);
    return () => clearTimeout(t);
  }, [done, navigate]);

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
      // 8/1: 原来这里 `alert()` 把临时密码明文回显一次。
      // 密码是管理员自己刚敲的, 回显不增加任何信息, 但会在屏幕上多挂一条
      // 明文 —— 而这一页正是投屏演示时最常打开的。
      // 密码就在上面那个输入框里, 需要的话在跳走之前复制即可。
      // 稍等一下再跳, 让"已创建"这句话被看见 —— 直接跳的话页面一闪而过,
      // 用户不确定到底成没成。真正的跳转在下面那个 useEffect 里 (要能撤)。
      setDone(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (done)
    return (
      <Card title="创建用户">
        <div style={{ fontSize: 13, color: "var(--status-ok)" }}>
          ✓ 已创建 {form.email} —— 他首次登录后必须改密。正在返回列表…
        </div>
      </Card>
    );

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


export function UserDetail() {
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
            <span style={{ display: "inline-flex", gap: 4 }}>
              {user.deleted_at && <Badge tone="err">已删</Badge>}
              {user.locked && <Badge tone="warn">已锁</Badge>}
              {!user.deleted_at && !user.locked && <Badge tone="ok">活跃</Badge>}
              {user.must_change_password && <Badge tone="warn">待改密</Badge>}
            </span>
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


export function UserEdit() {
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
