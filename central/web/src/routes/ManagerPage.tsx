/** /admin/departments — 部门视图 (manager+) (BL-ARCH1 5/10, 7/30 从 /manager 搬来).
 *
 * 看某个部门今日的 quota / audit / top 员工, 并能改日限额。
 *
 * ## 8/1: 拿掉中间那一层"选部门"页
 *
 * 改之前进这一页看到的是一整屏**只有四个方块**, 点一下跳到另一个 URL, 想换个
 * 部门还得点"← 选别的部门"跳回来。四个部门 = 一屏空白 + 两次跳转。
 *
 * 这正是 7/30 在 /admin 那一层已经拿掉过一次的东西 —— 当时的原话是
 * "中间夹了一层纯菜单页, 11 个磁贴本质就是一份左侧菜单, 只是被渲染成了
 * 一个页面" (见 AdminLayout 文件头)。同一个形状在这一页留到了现在。
 *
 * 现在: 部门变成顶部一排分段, 详情就在下面, 换部门是原地切换。
 *
 * **URL 保留** `/admin/departments/:dept`: 它是能发给人的深链接, 侧栏高亮
 * (navConfig 的 nested) 和 `/manager` 的老链接跳转都依赖它。切换时用
 * `replace` —— 不然点五个部门就往浏览器历史里塞五条, 按返回键要退五次
 * 才出得去。
 *
 * 没带部门时自动落到第一个, 而不是停在一个空页面上。
 */

import { useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { BTN_PRIMARY, DataTable, Section, Tabs, Toolbar } from "../components/DataTable";
import { PageShell } from "../components/PageShell";
import { RoleGate } from "../components/RoleGate";
import { Stat, StatBand } from "../components/StatBand";
import { adminApi } from "../lib/admin";
import {
  fetchDepartmentAudit,
  fetchDepartmentQuota,
  updateDepartmentQuota,
  type DepartmentAudit,
  type DepartmentQuota,
  DEPT_TOP_N,
} from "../lib/me";
import { getModelDisplay, isKnownModel, splitDisplayName } from "../lib/modelDisplay";
import { useAuthStore } from "../store/auth";

export function ManagerPage() {
  return (
    <RoleGate require={["manager", "admin"]}>
      <Routes>
        {/* 不带部门时由 DepartmentsView 自己重定向到第一个 —— 不能在这里
            写死, 因为"第一个是谁"要等 me / API 回来才知道。 */}
        <Route index element={<DepartmentsView />} />
        <Route path=":dept" element={<DepartmentsView />} />
      </Routes>
    </RoleGate>
  );
}

function DepartmentsView() {
  const me = useAuthStore((s) => s.me);
  const { dept } = useParams<{ dept: string }>();
  const navigate = useNavigate();
  // admin+ 没设 managed_departments 时, 从服务端取真实部门列表 (见下面 useEffect).
  const [allDepts, setAllDepts] = useState<string[] | null>(null);
  const [listErr, setListErr] = useState<string | null>(null);

  const isAdminOrAbove = me?.role === "admin" || me?.role === "sysadmin";
  const needsAll = !!isAdminOrAbove && me!.managed_departments.length === 0;

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

  // filter(Boolean): 部门名为空串时 `<Navigate to="">` 会解析回本页,
  // 变成无限 replace 循环 —— 页面直接卡死。空名字本身是脏数据, 但它
  // 不该表现成死循环。
  const depts = (
    me.managed_departments.length > 0 ? me.managed_departments : (allDepts ?? [])
  ).filter(Boolean);

  if (needsAll && allDepts === null && !listErr) {
    return (
      <PageShell>
        <Section>
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>加载中…</div>
        </Section>
      </PageShell>
    );
  }

  if (depts.length === 0)
    return <NoDepartments listErr={listErr} isAdmin={!!isAdminOrAbove} />;

  // URL 里没带部门 → 落到第一个。停在一个只有选择器的空页面上没有意义,
  // 那正是这次要拿掉的东西。replace 是为了不在历史里留下这个空状态 ——
  // 否则按返回键会先回到它, 再按一次才真的离开。
  if (!dept) return <Navigate to={encodeURIComponent(depts[0])} replace />;

  // URL 里的部门不在可管范围内: 明确说出来, 而不是渲染一屏空数据。
  // (手改 URL、或者授权被收回之后书签还在, 都会走到这里。)
  if (!depts.includes(dept)) {
    return (
      <PageShell>
        <Toolbar title="我管的部门" />
        <Section>
          <div style={{ fontSize: 12, lineHeight: 1.7 }}>
            你没有 <code>{dept}</code> 这个部门的权限，或者它不存在。
            <div style={{ marginTop: 6 }}>
              可管的是：
              {depts.map((d) => (
                <Link
                  key={d}
                  to={`/admin/departments/${encodeURIComponent(d)}`}
                  style={{ marginLeft: 8 }}
                >
                  {d}
                </Link>
              ))}
            </div>
          </div>
        </Section>
      </PageShell>
    );
  }

  return (
    <PageShell scroll="data">
      <Toolbar title="我管的部门">
        <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
          今日 quota / 用量 / top 员工
        </span>
      </Toolbar>

      {/* 部门选择。只有一个部门时不显示 —— 只有一项的分段器是纯噪音。 */}
      {depts.length > 1 && (
        <Tabs
          active={dept}
          onChange={(d) =>
            navigate(`/admin/departments/${encodeURIComponent(d)}`, { replace: true })
          }
          tabs={depts.map((d) => ({ key: d, label: d }))}
        />
      )}

      <DepartmentDetail dept={dept} />
    </PageShell>
  );
}

function NoDepartments({ listErr, isAdmin }: { listErr: string | null; isAdmin: boolean }) {
  return (
    <PageShell>
      <Toolbar title="我管的部门" />
      <Section>
        {/* 7/30: 老文案是"你是 {role}, 但没设 managed_departments. 找 admin 给你授权."
            对 sysadmin 说这话是荒唐的 —— 他就是最高权限, 没有"更上面的 admin"可找。
            而且它没说去哪儿设。授权入口是 /admin/users → 选人 → managed_departments。 */}
        <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.7 }}>
          {listErr ? (
            <>
              读部门列表失败：<code>{listErr}</code>
            </>
          ) : isAdmin ? (
            <>
              系统里还没有任何部门，或者你的账号没设{" "}
              <code>managed_departments</code>。
              <br />到 <Link to="/admin/users">用户管理</Link> 选中账号，在
              <code> managed_departments </code>里填部门名即可。
            </>
          ) : (
            <>
              你的账号还没被授权管理任何部门。找管理员在
              <b> 用户管理 → 你的账号 → managed_departments </b>里加上部门名。
            </>
          )}
        </div>
      </Section>
    </PageShell>
  );
}

function fmtTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}K`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

function DepartmentDetail({ dept }: { dept: string }) {
  const [quota, setQuota] = useState<DepartmentQuota | null>(null);
  const [audit, setAudit] = useState<DepartmentAudit | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editLimit, setEditLimit] = useState<string>("");
  const [busy, setBusy] = useState(false);
  /** 保存结果。原来这三处用的是 window.alert —— 一个要点掉的模态框, 而且
   *  跟整站风格完全不搭。就地反馈就够了。 */
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  /** 明细按哪个维度看。跟概览页 / 性能页 / 审计页同一套。 */
  const [detailTab, setDetailTab] = useState<"model" | "user">("model");

  useEffect(() => {
    // 切部门是原地换数据, 所以这里必须有 alive: 连着点两个部门时, 先发的
    // 那个响应可能后到, 于是标签停在 B 而数字是 A 的 —— 而且看不出来。
    let alive = true;
    setQuota(null);
    setAudit(null);
    setError(null);
    setNote(null);
    Promise.all([fetchDepartmentQuota(dept), fetchDepartmentAudit(dept)])
      .then(([q, a]) => {
        if (!alive) return;
        setQuota(q);
        setAudit(a);
        setEditLimit(String(q.day.limit));
      })
      .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [dept]);

  if (error)
    return (
      <Section>
        <div style={{ fontSize: 12, color: "var(--status-err)" }}>{error}</div>
      </Section>
    );
  if (!quota || !audit)
    return (
      <Section>
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>加载中…</div>
      </Section>
    );

  const handleUpdateQuota = async () => {
    const raw = editLimit.trim();
    const n = Number(raw);
    // 空串的 Number() 是 0, 会被静默当成"不限额" —— 这是老代码就有的坑。
    // 而 `!Number.isInteger` 挡掉 5.5 / 1e5 这类: 文案写着"整数",
    // 放过去也只是让后端 pydantic 回一个 422。
    if (raw === "" || !Number.isInteger(n) || n < 0) {
      setNote({
        ok: false,
        text: raw === "" ? "要设成不限额请填 0，留空不算" : "日限额必须是非负整数（0 = 不限）",
      });
      return;
    }
    setBusy(true);
    const r = await updateDepartmentQuota(dept, n);
    setBusy(false);
    if (!r.ok) {
      setNote({ ok: false, text: `保存失败：${r.detail}` });
      return;
    }
    setNote({
      ok: true,
      text:
        n === 0
          ? `已把 ${dept} 改成不限额`
          : `已把 ${dept} 的日限额改成 ${n.toLocaleString()}`,
    });
    fetchDepartmentQuota(dept)
      .then((q) => {
        // 切走了就别回写 —— 否则标签停在 B 而数字是 A 的, 看不出来。
        if (q.department !== dept) return;
        setQuota(q);
        // 回填服务端的实际值。不同步的话输入框还是刚才敲的那个数,
        // 而"保存"按钮靠 `editLimit === String(limit)` 判断有没有改动,
        // 于是服务端夹过值 (比如上限截断) 之后按钮会一直是可点的。
        setEditLimit(String(q.day.limit));
      })
      .catch((e) =>
        setNote({ ok: false, text: `保存成功, 但刷新失败: ${String(e)}` }),
      );
  };

  const pct = quota.day.limit > 0 ? (quota.day.used / quota.day.limit) * 100 : null;

  return (
    <>
      <Section>
        <StatBand>
          <Stat hero label="今日已用" value={`${fmtTokens(quota.day.used)} tok`} />
          <Stat
            label="占日限额"
            value={pct === null ? "—" : `${pct.toFixed(0)}%`}
            hint={quota.day.limit > 0 ? undefined : "未设限额"}
          />
          <Stat label="请求数" value={audit.request_count.toLocaleString()} />
          <Stat label="用量合计" value={`${fmtTokens(audit.total_tokens)} tok`} />
          <Stat
            label="活跃员工"
            // ⚠ by_user 后端是 LIMIT 10, 所以这个数最多到 10。
            // 直接显示 `by_user.length` 的话, 11 人以上的部门永远显示"10",
            // 而界面上没有任何东西说这是个上限。
            value={
              audit.by_user.length >= DEPT_TOP_N.user
                ? `${DEPT_TOP_N.user}+`
                : audit.by_user.length
            }
            hint={
              audit.by_user.length >= DEPT_TOP_N.user
                ? `只统计用量前 ${DEPT_TOP_N.user} 名`
                : undefined
            }
          />
        </StatBand>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            marginTop: 8,
            paddingTop: 8,
            borderTop: "1px solid var(--border-soft)",
            fontSize: 12,
          }}
        >
          <span style={{ color: "var(--text-muted)" }}>日限额</span>
          <input
            type="number"
            value={editLimit}
            onChange={(e) => {
              setEditLimit(e.target.value);
              // 一改就清掉上一条结果 —— 否则把 5000 改成 8000 还没保存时,
              // 旁边仍挂着绿色的"已改成 5,000"。
              setNote(null);
            }}
            style={{
              width: 120,
              padding: "3px 6px",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm)",
              background: "var(--bg-elev)",
              color: "var(--text)",
              fontSize: 12,
            }}
          />
          <button
            onClick={handleUpdateQuota}
            disabled={busy || editLimit === String(quota.day.limit)}
            style={BTN_PRIMARY}
            title={
              editLimit === String(quota.day.limit)
                ? "跟当前值一样, 没有要保存的"
                : undefined
            }
          >
            {busy ? "保存中…" : "保存"}
          </button>
          <span style={{ color: "var(--text-muted)", fontSize: 11 }}>0 = 不限</span>
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

      {/* 8/1: 原来是"按模型 / 按员工"两张表并排 1fr 1fr。这正是概览页、
          性能页、审计页三处注释反复批判的形状 —— 并排时每张只分到一半宽,
          而这两张里都有模型显示名和邮箱这种长文本, 结果是每个框内部各自
          横向滚动。窄一点的窗口上更明显。
          改成跟那三页同一套分段切换。 */}
      <Section fill>
        <div style={{ marginBottom: 6 }}>
          <Tabs
            active={detailTab}
            onChange={setDetailTab}
            tabs={[
              { key: "model", label: `按模型 ${audit.by_model.length}` },
              {
                key: "user",
                label: `按员工 ${
                  audit.by_user.length >= DEPT_TOP_N.user
                    ? `${DEPT_TOP_N.user}+`
                    : audit.by_user.length
                }`,
              },
            ]}
          />
        </div>

        {detailTab === "model" ? (
          <DataTable
            fill
            rows={audit.by_model}
            rowKey={(m) => m.model}
            empty="本部门今日还没有调用"
            footer={
              audit.by_model.length >= DEPT_TOP_N.model
                ? `只列用量最高的 ${DEPT_TOP_N.model} 个模型`
                : undefined
            }
            columns={[
              {
                header: "模型",
                cell: (m) => {
                  // 认不出的显示原始 id —— 兜底 friendly 是固定的"未知模型",
                  // 几个下线过的模型会全塌成同一行 (跟概览页/审计页一致)。
                  if (!isKnownModel(m.model))
                    return <code style={{ fontSize: 11 }}>{m.model || "(未知)"}</code>;
                  const d = getModelDisplay(m.model);
                  return (
                    <span title={`${d.friendly}\n${m.model}`}>
                      {d.dotEmoji} {splitDisplayName(d.friendly).name}
                    </span>
                  );
                },
                truncate: true,
              },
              {
                header: "次数",
                cell: (m) => m.count.toLocaleString(),
                align: "right",
                width: 80,
              },
              {
                header: "tokens",
                cell: (m) => fmtTokens(m.total_tokens),
                align: "right",
                width: 96,
              },
            ]}
          />
        ) : (
          <DataTable
            fill
            rows={audit.by_user}
            rowKey={(u) => u.user_email}
            empty="本部门今日还没有调用"
            footer={
              // 后端这条是 LIMIT 10 —— 不写出来的话, 正好 10 个人时没人分得清
              // 是"部门就 10 个人"还是"被截到 10 个"。
              audit.by_user.length >= DEPT_TOP_N.user
                ? `只列用量最高的 ${DEPT_TOP_N.user} 人 —— 不在表里不代表没用过`
                : undefined
            }
            columns={[
              {
                header: "员工",
                cell: (u) => <span title={u.user_email}>{u.user_email}</span>,
                truncate: true,
              },
              {
                header: "次数",
                cell: (u) => u.count.toLocaleString(),
                align: "right",
                width: 80,
              },
              {
                header: "tokens",
                cell: (u) => fmtTokens(u.total_tokens),
                align: "right",
                width: 96,
              },
            ]}
          />
        )}
      </Section>
    </>
  );
}
