/** /admin/users — 完整用户管理 (BL-ARCH1 P1 5/10).
 *
 * sysadmin / admin 都能进, 但能力不同:
 *   - sysadmin: 列出含 sysadmin, 能创建 admin / sysadmin
 *   - admin: 屏蔽 sysadmin 行, 只能创建 employee / manager
 *
 * ## 8/1
 *
 * **一屏布局。** 跟其余后台页对齐: 标题和筛选条不动, 只有表身滚, 表头 sticky。
 * Card + 手搓 <table> (13px / 6px 内边距) 换成共享的 Toolbar / Section /
 * DataTable。两份私有徽章 (RoleBadge / Badge) 退役, 走共享 Badge。
 *
 * **六个原生弹窗换掉。** 这一页原来有 5 个 alert + 1 个 prompt + 1 个 confirm,
 * 是全仓最多的一处。其中 `prompt()` 收密码尤其糟:
 *
 *   · 浏览器的 prompt **不遮蔽输入** —— 密码明文显示在屏幕上, 而这一页
 *     是管理员在会议里投屏演示时最容易打开的
 *   · 紧接着又 `alert()` 把同一个密码回显一次
 *   · prompt 的值可能进浏览器的表单历史
 *
 * 现在是一个真对话框 (type=password + 生成按钮 + 复制), 结果就地提示。
 *
 * 三个表单页 (创建/详情/编辑) 8/1 拆到了 UserForms.tsx —— 这个文件当时
 * 743 行, 再加对话框会撞军规 §1 的 800 行红线。
 */

import { useEffect, useState } from "react";
import { Link, Route, Routes } from "react-router-dom";

import {
  Badge,
  BTN,
  BTN_DANGER,
  BTN_PRIMARY,
  DataTable,
  Section,
  Toolbar,
  type Column,
} from "../../components/DataTable";
import { ConfirmDialog } from "../../components/Dialog";
import { PageShell, Stale } from "../../components/PageShell";
import { adminApi, type Role, type UserBrief } from "../../lib/admin";
import { useAuthStore } from "../../store/auth";
import { UserCreate, UserDetail, UserEdit } from "./UserForms";
import { RoleBadge, copyText, genTempPassword } from "./usersShared";

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

/** 当前挂着的对话框。null = 没有。 */
type Pending =
  | { kind: "delete"; user: UserBrief }
  | { kind: "reset"; user: UserBrief }
  | null;

function UsersList() {
  const me = useAuthStore((s) => s.me);
  const [users, setUsers] = useState<UserBrief[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [roleFilter, setRoleFilter] = useState<Role | "">("");
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [pending, setPending] = useState<Pending>(null);
  const [busy, setBusy] = useState(false);
  /** 操作结果。原来这些全是 window.alert。 */
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

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

  const act = async (label: string, fn: () => Promise<void>) => {
    setBusy(true);
    // 上一条结果先清掉 —— 不清的话"删除失败: xxx"会一直挂在筛选条里,
    // 下一次操作成功了它还在, 看起来像刚失败。
    setNote(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setNote({ ok: false, text: `${label}失败：${e instanceof Error ? e.message : String(e)}` });
    } finally {
      setBusy(false);
    }
  };

  const columns: Column<UserBrief>[] = [
    {
      header: "Email",
      // 点进详情是**跳页**, 所以这里必须是真 <Link> —— 只挂 onRowClick 的话
      // 中键/⌘+点击开新标签、右键"在新标签打开"、悬停看目标 URL 全都没了。
      cell: (u) => (
        <Link to={`/admin/users/${encodeURIComponent(u.email)}`} title={u.email}>
          {u.email}
        </Link>
      ),
      truncate: true,
      width: 240,
    },
    { header: "名字", cell: (u) => u.name || "-", truncate: true, width: 120 },
    {
      header: "部门",
      cell: (u) => <span style={{ color: "var(--text-muted)" }}>{u.department || "-"}</span>,
      truncate: true,
      width: 120,
    },
    { header: "Role", cell: (u) => <RoleBadge role={u.role} />, width: 90 },
    {
      header: "状态",
      cell: (u) => (
        <span style={{ display: "inline-flex", gap: 4 }}>
          {u.deleted_at && <Badge tone="err">已删</Badge>}
          {u.locked && <Badge tone="warn">已锁</Badge>}
          {!u.deleted_at && !u.locked && <Badge tone="ok">活跃</Badge>}
          {u.must_change_password && <Badge tone="warn">待改密</Badge>}
        </span>
      ),
      width: 150,
    },
    {
      header: "",
      align: "right",
      width: 210,
      cell: (u) =>
        u.deleted_at ? null : (
          <span style={{ display: "inline-flex", gap: 4 }}>
            <button
              style={BTN}
              disabled={busy}
              onClick={() =>
                void act(u.locked ? "解锁" : "锁定", async () => {
                  await adminApi.lockUser(u.email, !u.locked);
                })
              }
            >
              {u.locked ? "解锁" : "锁"}
            </button>
            <button style={BTN} disabled={busy} onClick={() => setPending({ kind: "reset", user: u })}>
              重置密码
            </button>
            <Link
              to={`/admin/users/${encodeURIComponent(u.email)}/edit`}
              style={{ ...BTN, textDecoration: "none", color: "var(--text)" }}
            >
              编辑
            </Link>
            <button
              style={BTN_DANGER}
              disabled={busy}
              onClick={() => setPending({ kind: "delete", user: u })}
            >
              删
            </button>
          </span>
        ),
    },
  ];

  return (
    <PageShell scroll="data">
      <Toolbar
        title={
          // 数量三态: 加载中 / 读取失败 / 真实数字。
          // 失败时不给数字 —— 理由见 refresh() 的 catch 分支。
          `用户 · ${error ? "读取失败" : loading ? "加载中…" : `${users.length} 人`}`
        }
      >
        <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
          {me?.role === "sysadmin" ? "sysadmin 视角，含 sysadmin" : "admin 视角，不含 sysadmin"}
        </span>
        <Link to="/admin/users/new" style={{ ...BTN_PRIMARY, textDecoration: "none" }}>
          + 创建用户
        </Link>
      </Toolbar>

      {/* 筛选条留在滚动区外面 —— 滚到第 40 行才想起要改条件时, 不该先滚回顶部。 */}
      <Section>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input
            type="search"
            placeholder="搜 email / 名字 / 部门…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            style={{ ...INPUT, flex: 1, minWidth: 200 }}
          />
          <select
            value={roleFilter}
            onChange={(e) => setRoleFilter(e.target.value as Role | "")}
            style={INPUT}
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
              fontSize: 12,
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
          <button style={BTN} onClick={() => void refresh()} disabled={loading}>
            {loading ? "刷新中…" : "刷新"}
          </button>
          {note && (
            <span
              style={{
                fontSize: 11,
                color: note.ok ? "var(--status-ok)" : "var(--status-err)",
              }}
            >
              {note.text}
            </span>
          )}
        </div>
      </Section>

      {error && (
        <Section>
          <div style={{ fontSize: 12, color: "var(--status-err)" }}>{error}</div>
        </Section>
      )}

      {!error && (
        <Section
          fill
          title={filter ? `筛后 ${filtered.length} / ${users.length}` : undefined}
        >
          {/* 切 role / 勾"含已删"会重新请求, 而表里还是上一批的行 ——
              可以对一个马上要消失的行点"锁"。压暗禁点, 跟日志页/审计页同一套。 */}
          <Stale fill loading={loading && users.length > 0}>
          <DataTable
            fill
            columns={columns}
            rows={filtered}
            rowKey={(u) => u.email}
            // 已删的行整体压淡。旧版是 <tr style={{opacity: .5}}>,
            // 换成 DataTable 之后一度没了 —— 勾上"含已删"后, 已删行跟
            // 活跃行除了多一个红徽章之外长得一模一样。
            rowStyle={(u) => (u.deleted_at ? { opacity: 0.5 } : undefined)}
            empty={
              loading
                ? "加载中…"
                : filter || roleFilter
                  ? "没有匹配的用户 —— 换个搜索词或清掉 role 筛选"
                  : "还没有用户。点右上角「+ 创建用户」。"
            }
          />
          </Stale>
        </Section>
      )}

      {pending?.kind === "delete" && (
        <ConfirmDialog
          danger
          title={`删除 ${pending.user.email}?`}
          confirmLabel="删除"
          busy={busy}
          onCancel={() => setPending(null)}
          onConfirm={() => {
            const u = pending.user;
            setPending(null);
            void act("删除", async () => {
              await adminApi.deleteUser(u.email);
              setNote({ ok: true, text: `已删除 ${u.email}（软删，可恢复）` });
            });
          }}
        >
          软删除，勾上「含已删」还能看到这个账号。
          <div style={{ marginTop: 6, color: "var(--text-muted)" }}>
            他的历史用量记录不受影响 —— 审计数据按 email 存，跟账号是否存在无关。
          </div>
        </ConfirmDialog>
      )}

      {pending?.kind === "reset" && (
        <ResetPasswordDialog
          user={pending.user}
          onCancel={() => setPending(null)}
          onDone={(text) => {
            setPending(null);
            setNote({ ok: true, text });
            void refresh();
          }}
        />
      )}

    </PageShell>
  );
}

/** 重置密码。
 *
 * 换掉 `window.prompt` 的理由见文件头 —— 核心是 prompt **不遮蔽输入**,
 * 密码会明文显示在屏幕上。
 *
 * ⚠ 失败和 busy 都是这个组件**自己**的状态, 不往上抛给页面级的 note。
 * 第一版抛上去了, 结果是: 请求失败时对话框原样开着、里面毫无变化, 而
 * 错误文字出现在**被遮罩盖住**的筛选条里 —— 用户看到的是"点了没反应"。
 */
function ResetPasswordDialog({
  user,
  onCancel,
  onDone,
}: {
  user: UserBrief;
  onCancel: () => void;
  onDone: (text: string) => void;
}) {
  const [pwd, setPwd] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  /** null = 还没点过复制。 */
  const [copied, setCopied] = useState<boolean | null>(null);
  const tooShort = pwd.length < 8;

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await adminApi.resetPassword(user.email, pwd, true);
      // ⚠ 不在提示里回显密码。管理员刚敲完, 自己知道; 而这一页经常在
      // 投屏演示的场景下打开, 一条挂在界面上的明文密码谁都能拍下来。
      onDone(`已重置 ${user.email} 的密码，他下次登录必须改密`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ConfirmDialog
      title={`重置 ${user.email} 的密码`}
      confirmLabel="重置"
      busy={busy}
      // 没填够 8 位时按钮就是禁用的 —— 而不是点了之后在 onConfirm 里
      // 悄悄 return。后者正是 Dialog 文件头骂的"看起来像功能坏了"。
      confirmDisabled={tooShort}
      onCancel={onCancel}
      onConfirm={() => void submit()}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ display: "flex", gap: 6 }}>
          <input
            type={show ? "text" : "password"}
            value={pwd}
            onChange={(e) => {
              setPwd(e.target.value);
              setCopied(null);
              setErr(null);
            }}
            placeholder="临时密码，至少 8 位"
            autoComplete="new-password"
            style={{ ...INPUT, flex: 1 }}
          />
          <button type="button" style={BTN} onClick={() => setShow((v) => !v)}>
            {show ? "隐藏" : "显示"}
          </button>
          <button
            type="button"
            style={BTN}
            onClick={() => {
              setPwd(genTempPassword());
              setCopied(null);
              setErr(null);
            }}
          >
            生成
          </button>
          {/* 这个密码要发给本人, 而它只在这里出现一次。copyText 在内网 http
              下会退回 execCommand —— navigator.clipboard 只在安全上下文里存在,
              而客户内网经常是 http://10.x.x.x 直连。 */}
          <button
            type="button"
            style={BTN}
            disabled={!pwd}
            onClick={() => void copyText(pwd).then(setCopied)}
          >
            {copied === true ? "已复制" : copied === false ? "复制失败" : "复制"}
          </button>
        </div>
        {err ? (
          <div style={{ fontSize: 11, color: "var(--status-err)" }}>重置失败：{err}</div>
        ) : (
          <div
            style={{
              fontSize: 11,
              color: pwd && tooShort ? "var(--status-err)" : "var(--text-muted)",
            }}
          >
            {pwd && tooShort
              ? `还差 ${8 - pwd.length} 位`
              : copied === false
                ? "复制失败（浏览器不允许），请手动选中上面的密码复制。"
                : "重置后该账号下次登录必须改密。这个密码只在这里显示一次，记得先发给本人。"}
          </div>
        )}
      </div>
    </ConfirmDialog>
  );
}

const INPUT: React.CSSProperties = {
  background: "var(--bg-elev)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  padding: "3px 8px",
  color: "var(--text)",
  fontSize: 12,
};
