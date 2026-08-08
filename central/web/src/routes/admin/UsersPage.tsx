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
 * 创建页和编辑弹窗在 UserForms.tsx (8/1 拆的, 当时这个文件 743 行,
 * 再加对话框会撞军规 §1 的 800 行红线)。
 *
 * **编辑不跳页** —— 点「编辑」是在这一页上开弹窗, URL 变成
 * `/admin/users/:email` (语义是"选中了谁", 跟 /admin/departments/:dept
 * 一致)。为什么从跳页改过来, 见 UserForms.tsx 里 UserEditDialog 的注释。
 */

import { useEffect, useState } from "react";
import {
  Link,
  Navigate,
  Route,
  Routes,
  useNavigate,
  useLocation,
  useParams,
  useSearchParams,
} from "react-router-dom";

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
import { UserCreate, UserEditDialog } from "./UserForms";
import { RoleBadge, copyText, genTempPassword } from "./usersShared";

export function UsersPage() {
  return (
    <Routes>
      {/* 8/1: `:email` 不再是"详情页", 而是"列表 + 选中了谁"—— 列表照常渲染,
          上面盖一个编辑弹窗。语义跟 /admin/departments/:dept 一致。
          详情页删了 (它只比列表多四个字段, 而且是个死路), 那几个字段现在
          在弹窗顶部的只读区。理由详见 UserForms.tsx 文件头。 */}
      {/* ⚠ 一条 `:email?` 而不是 index + `:email` 两条。
          两条 Route 渲染同一个组件时, react-router 恰好会复用实例 (它建
          RenderedRoute 时没传 key), 所以列表 state 不重置、不重新请求。
          但那是**巧合不是保证** —— 哪天有人给其中一条包一层 wrapper,
          实例就会重挂载, 表现是"保存成功但那句提示一闪不见 + 多打一次
          接口", 而且不报错。可选段让它变成结构上的同一条路由。
          ("new" 是静态段, 排序上仍然优先于 `:email?"。) */}
      <Route path="new" element={<UserCreate />} />
      <Route path=":email?" element={<UsersList />} />
      {/* 老链接 (书签 / 别人发过来的) 不能坏。 */}
      <Route path=":email/edit" element={<RedirectToUser />} />
    </Routes>
  );
}

/** `/admin/users/:email/edit` → `/admin/users/:email`。
 *  用 replace —— 这个中转 URL 不该留在浏览器历史里, 否则按返回会回到它,
 *  然后又被重定向, 等于返回键失灵。 */
function RedirectToUser() {
  const { email = "" } = useParams<{ email: string }>();
  // search 要带上 —— 不带的话从带筛选的链接点进来, 重定向一次筛选就没了。
  const { search } = useLocation();
  return (
    <Navigate to={`/admin/users/${encodeURIComponent(email)}${search}`} replace />
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
  const navigate = useNavigate();
  /** URL 里选中的人。有值 = 编辑弹窗开着。 */
  const { email: selected } = useParams<{ email: string }>();
  const [users, setUsers] = useState<UserBrief[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // ── 筛选放 URL, 不放组件 state (8/1) ──────────────────────────
  //
  // 原来三个筛选都是 useState, 于是**离开这一页就全丢**: 搜了
  // "engineering" 点进编辑, 回来搜索框是空的, 得重新搜一遍。这正是
  // "跳来跳去体验差"的一半。
  //
  // 放 URL 之后: 弹窗开关不影响它 (query 跟着 pathname 一起在), 刷新不丢,
  // 而且筛选结果能直接把链接发给人。
  const [sp, setSp] = useSearchParams();
  const filter = sp.get("q") ?? "";
  // ⚠ 必须归一化, 不能只做类型断言。`?role=garbage` 时:
  //   · 后端不校验枚举, 参数化 SQL 直接返 200 + 空列表 (不是 400)
  //   · <select value="garbage"> 没有对应 option → selectedIndex = -1
  //     → **筛选框显示成空白**, 跟"全部 role"长得一样
  // 结果是"看起来没筛选, 却一个人都查不到", 而且没有任何东西说得清。
  const rawRole = sp.get("role") ?? "";
  const roleFilter: Role | "" = (["sysadmin", "admin", "manager", "employee"] as const).includes(
    rawRole as Role,
  )
    ? (rawRole as Role)
    : "";
  const includeDeleted = sp.get("deleted") === "1";

  /** 改一项筛选。空值就把这个 key 从 URL 里删掉 —— 不然地址栏会挂着
   *  `?q=&role=&deleted=` 一串空参数, 分享出去也难看。
   *  用 replace: 每敲一个字母都往历史里塞一条的话, 返回键就废了。 */
  const setParam = (key: string, val: string) => {
    const next = new URLSearchParams(sp);
    if (val) next.set(key, val);
    else next.delete(key);
    setSp(next, { replace: true });
  };

  /** 搜索框的即时值。URL 是慢一拍写的 —— 见下面。 */
  const [q, setQ] = useState(filter);
  // URL 变了 (浏览器前进后退 / 别人发来的链接) 要同步回输入框。
  useEffect(() => setQ(filter), [filter]);
  // ⚠ 写 URL 要防抖。每敲一个字母一次 replaceState 的话:
  //   · Safari 对 pushState/replaceState 有 30 秒 100 次的硬限流, 超了抛
  //     SecurityError —— 手快的人连打 100 个字符 (含退格) 够得着
  //   · 中文输入法组合期间每个字母都会写一次, 地址栏一直在抖
  // 输入框用本地 state 即时回显, URL 落后 250ms, 两边都不难受。
  useEffect(() => {
    if (q === filter) return;
    const t = setTimeout(() => setParam("q", q), 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

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

  /** 关弹窗 = 回到列表 URL, **带着当前筛选**。
   *  丢了 query 的话关一次弹窗筛选就没了, 等于白改。 */
  const closeDialog = () => {
    const q = sp.toString();
    navigate(`/admin/users${q ? `?${q}` : ""}`, { replace: true });
  };

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
      // 仍然是真 <Link> —— 中键/⌘+点击开新标签、右键"在新标签打开"、悬停看
      // 目标 URL 都要保留。只是目标从"详情页"变成了"列表 + 选中这个人",
      // 所以点它是原地开弹窗, 不跳页。
      cell: (u) => (
        <Link
          to={`/admin/users/${encodeURIComponent(u.email)}${sp.toString() ? `?${sp}` : ""}`}
          title={u.email}
        >
          {u.email}
        </Link>
      ),
      truncate: true,
      // 8/8 前五列改百分比: 表格是 table-layout:fixed, 纯 px 的列在窄窗口下
      // 不缩, 六列 930px 塞不进 760px 的面板, 最右边那组按钮就被顶出可视区
      // (/admin/providers 那次就是这么丢的)。百分比跟着表宽一起缩。
      width: "26%",
    },
    { header: "名字", cell: (u) => u.name || "-", truncate: true, width: "13%" },
    {
      header: "部门",
      cell: (u) => <span style={{ color: "var(--text-muted)" }}>{u.department || "-"}</span>,
      truncate: true,
      width: "13%",
    },
    { header: "Role", cell: (u) => <RoleBadge role={u.role} />, width: "10%" },
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
      width: "16%",
    },
    {
      header: "",
      align: "right",
      // 这一列**留 px**: 里面是「锁 / 重置密码 / 编辑 / 删」四个控件, 实测
      // 就得 210px, 按比例缩下去按钮会互相挤掉。前五列 78% 会替它让位。
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
              to={`/admin/users/${encodeURIComponent(u.email)}${sp.toString() ? `?${sp}` : ""}`}
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
        <Link
          // 带上当前筛选 —— 创建页的"取消"要能回到你原来那个视图。
          to={`/admin/users/new${sp.toString() ? `?${sp}` : ""}`}
          style={{ ...BTN_PRIMARY, textDecoration: "none" }}
        >
          + 创建用户
        </Link>
      </Toolbar>

      {/* 筛选条留在滚动区外面 —— 滚到第 40 行才想起要改条件时, 不该先滚回顶部。 */}
      <Section>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input
            type="search"
            placeholder="搜 email / 名字 / 部门…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            style={{ ...INPUT, flex: 1, minWidth: 200 }}
          />
          <select
            value={roleFilter}
            onChange={(e) => setParam("role", e.target.value)}
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
              onChange={(e) => setParam("deleted", e.target.checked ? "1" : "")}
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

      {/* 编辑弹窗。URL 里有 :email 就开着 —— 所以深链接 / 刷新 / 分享出去的
          链接都能直接落到"选中某个人"的状态, 而不需要额外的路由。 */}
      {selected && (
        <UserEditDialog
          // key: 换一个人时要重新挂载, 否则上一个人的表单草稿会留在里面。
          key={selected}
          email={selected}
          onClose={closeDialog}
          onSaved={(msg) => {
            closeDialog();
            setNote({ ok: true, text: msg });
            void refresh();
          }}
        />
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
