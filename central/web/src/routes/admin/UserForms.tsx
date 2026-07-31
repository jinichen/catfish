/** /admin/users 的创建页 + 编辑弹窗 (8/1 从 UsersPage 拆出).
 *
 * 拆的原因是 UsersPage 到了 743 行, 而这次要给它加"重置密码"对话框
 * (原来是 window.prompt) —— 加完会撞 CLAUDE.md 军规 §1 的 800 行红线。
 *
 * 拆的切口是"列表页 vs 表单页": 前者是一屏布局 + 密集表格, 后者是竖排
 * 表单, 两边的关注点本来就不一样。共用的只有 adminApi 和 RoleBadge。
 *
 * 8/1 晚些时候: 原来的"编辑页"和"详情页"合并成了一个列表上的弹窗
 * (`UserEditDialog`), 详情页删掉。理由见那个组件的注释。
 * 这里只剩 `UserCreate` 还是独立页 —— 新建是"离开列表去做一件新事",
 * 语义上该有自己的地址, 而且它的流程 (填 → 保存 → 提示 → 回列表) 本来
 * 就是单向的, 没有"跳来跳去"的毛病。
 *
 * ⚠ 创建页**没有跟着改密度**: 还是 Card + 13px 表单。表单要的是好填,
 * 不是塞得下, 硬套密集表格的尺寸反而更难用。
 */

import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { Card } from "../../components/Card";
import { ConfirmDialog, Modal } from "../../components/Dialog";
import { Badge, BTN, BTN_PRIMARY } from "../../components/DataTable";
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
  /** 进来时列表的筛选。取消要能原样回去 —— 不带的话点一次取消, 刚才
   *  搜的条件就没了。 */
  const [sp] = useSearchParams();
  const backToList = `/admin/users${sp.toString() ? `?${sp}` : ""}`;
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
    // ⚠ 成功后回**不带筛选**的列表 (backToList 是给"取消"用的)。
    // 刚建的人多半不匹配你之前的搜索词, 带着筛选回去会看不到他 ——
    // 而"创建成功"之后第一件事就是确认他在列表里。
    // replace: 提交完的表单不该留在浏览器历史里 —— 不然回到列表按一下
    // 返回键又是那个刚填完的创建页。这跟编辑侧 closeDialog 用 replace
    // 是同一条理由, 第一版只改了编辑那边, 创建这边漏了。
    const t = setTimeout(() => navigate("/admin/users", { replace: true }), 900);
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
            onClick={() => navigate(backToList, { replace: true })}
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


// ── 编辑 (弹窗) ──────────────────────────────────────────────────

/** 编辑一个用户 —— **列表页上的弹窗, 不跳页**.
 *
 * ## 8/1: 为什么从"跳页"改成"弹窗"
 *
 * 鸿波: "用户编辑跳来跳去的逻辑很奇怪, 体验很差"。把实际跳转画出来之后
 * 确实很奇怪:
 *
 *     列表 ──点"编辑"──→ 编辑页 ──保存成功──→ **详情页**
 *                                              ↑ 而详情页**没有任何
 *                                                返回列表的入口**
 *
 * 于是改完一个用户之后人是被卡住的: 想回列表只能按浏览器后退, 而后退
 * 回到的是**刚保存完的那个编辑表单**, 再按一次才到列表。
 *
 * "取消"更怪 —— 它也 navigate 到详情页, 也就是说从列表点进来、点取消,
 * 会到一个自己从没去过的页面; 而且是 push 不是回退, 历史越点越深。
 *
 * 另外列表的搜索和筛选是组件 state, 跳走就没 —— 搜了 "engineering" 点编辑,
 * 回来搜索框是空的, 得重新搜一遍。
 *
 * 现在: 编辑在列表上开一个弹窗, 全程不离开列表。筛选、滚动位置天然保留,
 * 关掉弹窗就在原地。
 *
 * ## 详情页去哪了
 *
 * 删了。它只比列表多四个字段 (管的部门 / 创建于 / 最后登录 / 锁定时间),
 * 而 email、名字、部门、角色、状态列表里全都有 —— 为了四个字段单开一页,
 * 还是个死路。那四个字段现在是这个弹窗顶部的只读区。
 *
 * URL 保留 `/admin/users/:email` 作为深链接 (语义变成"选中了谁", 跟
 * `/admin/departments/:dept` 一致); 老的 `/:email/edit` 重定向过去。
 */
export function UserEditDialog({
  email,
  onClose,
  onSaved,
}: {
  email: string;
  /** 关闭 (取消 / Esc / 点遮罩)。 */
  onClose: () => void;
  /** 保存成功。调用方负责关弹窗 + 刷新列表。 */
  onSaved: (msg: string) => void;
}) {
  const me = useAuthStore((s) => s.me);
  const [user, setUser] = useState<UserBrief | null>(null);
  /** 服务端那一份, 用来判断"改了没"。 */
  const [orig, setOrig] = useState<UserBrief | null>(null);
  /** ⚠ 加载失败和保存失败**必须分开**。
   *
   * 原来共用一个 `error`, 而组件顶部是 `if (error) return <div>{error}</div>`
   * —— 保存失败时整个表单被一行红字替换掉, 填的内容全丢, 页面上连个返回
   * 按钮都没有。而表单里那句 `{error && ...}` 是**永远执行不到的死代码**
   * (早返回在它前面)。 */
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isSysadmin = me?.role === "sysadmin";
  /** 已删的账号不给编辑 —— 后端 update_user 见到 deleted_at 会返 400,
   *  让人填完再撞是没必要的。列表的操作列本来就对已删行不显示按钮,
   *  但 Email 那一列的链接没排除, 从那儿点进来就会落到这里。 */
  const readOnly = !!user?.deleted_at;
  /** 有改动时确认再关。Esc 和点遮罩这两条路原来一个字都不说, 改了五个
   *  字段误点一下遮罩就全没了 —— 而底部按钮明明已经把文案换成了
   *  「放弃改动」。三条出口应该是一致的。 */
  const [confirmClose, setConfirmClose] = useState(false);

  useEffect(() => {
    let alive = true;
    setUser(null);
    setOrig(null);
    setLoadErr(null);
    setSaveErr(null);
    adminApi
      .getUser(email)
      .then((u) => {
        if (!alive) return;
        setUser(u);
        setOrig(u);
      })
      .catch((e) => alive && setLoadErr(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [email]);

  const changed =
    !!user &&
    !!orig &&
    (user.name !== orig.name ||
      user.department !== orig.department ||
      user.role !== orig.role ||
      user.managed_departments.join(",") !== orig.managed_departments.join(","));

  const handleClose = () => {
    if (changed && !busy) setConfirmClose(true);
    else onClose();
  };

  const save = async () => {
    if (!user || !changed || readOnly) return;
    setBusy(true);
    setSaveErr(null);
    try {
      await adminApi.updateUser(user.email, {
        name: user.name,
        department: user.department,
        role: user.role,
        managed_departments: user.managed_departments,
      });
      onSaved(`已保存 ${user.email}`);
    } catch (e) {
      // 留在弹窗里 —— 抛到列表上的话它正被遮罩盖着, 看起来像"点了没反应"。
      setSaveErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      width={520}
      title={loadErr ? "打不开这个用户" : (user?.email ?? "加载中…")}
      titleExtra={user ? <RoleBadge role={user.role} /> : undefined}
      // 数据到了才抓焦点 —— 不传的话焦点会停在底部的「关闭」按钮上,
      // 顺手一个 Enter 就把弹窗关了 (见 Modal 里 focus effect 的说明)。
      ready={!!user}
      // 请求进行中不让关 —— 关了之后请求还在飞, 结果无处可去。
      lockReason={busy ? "保存中" : undefined}
      // 在输入框里按 Enter 直接保存。旧的编辑页是真 <form>, 有这个手感,
      // 换成弹窗不该丢。
      onSubmit={() => {
        if (changed && !busy) void save();
      }}
      onClose={handleClose}
      footer={
        <>
          {/* 未保存的改动要说出来, 而不是让人点了取消才发现丢了。 */}
          {changed && !busy && (
            <span
              style={{ fontSize: 11, color: "var(--status-warn)", marginRight: "auto" }}
            >
              有未保存的改动
            </span>
          )}
          <button style={BTN} onClick={handleClose} disabled={busy}>
            {changed ? "放弃改动" : "关闭"}
          </button>
          <button
            style={{
              ...BTN_PRIMARY,
              ...(changed && !busy ? null : { opacity: 0.45, cursor: "not-allowed" }),
            }}
            disabled={!changed || busy || readOnly}
            // 没改动 / 已删账号时按钮是禁用的, 而不是点了之后静默什么都不做。
            title={
              readOnly ? "这个账号已删除, 不能改" : changed ? undefined : "还没有改动"
            }
            onClick={() => void save()}
          >
            {busy ? "保存中…" : "保存"}
          </button>
        </>
      }
    >
      {loadErr ? (
        <div style={{ fontSize: 12, color: "var(--status-err)" }}>{loadErr}</div>
      ) : !user ? (
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>加载中…</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
          {/* 只读区 —— 原来这几个字段是独立"详情页"的全部价值。 */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "auto 1fr",
              gap: "2px 12px",
              fontSize: 11,
              color: "var(--text-muted)",
              background: "var(--bg-secondary)",
              padding: "6px 10px",
              borderRadius: "var(--radius-sm)",
            }}
          >
            <span>状态</span>
            <span style={{ display: "inline-flex", gap: 4 }}>
              {user.deleted_at && <Badge tone="err">已删</Badge>}
              {user.locked && <Badge tone="warn">已锁</Badge>}
              {!user.deleted_at && !user.locked && <Badge tone="ok">活跃</Badge>}
              {user.must_change_password && <Badge tone="warn">待改密</Badge>}
            </span>
            <span>创建于</span>
            <span style={{ fontFamily: "var(--font-mono)" }}>
              {user.created_at?.slice(0, 19) || "-"}
            </span>
            <span>最后登录</span>
            <span style={{ fontFamily: "var(--font-mono)" }}>
              {user.last_login_at?.slice(0, 19) || "从未"}
            </span>
            {user.locked_at && (
              <>
                <span>锁定于</span>
                <span style={{ fontFamily: "var(--font-mono)" }}>
                  {user.locked_at.slice(0, 19)}
                </span>
              </>
            )}
          </div>

          <Field label="姓名">
            <input
              type="text"
              readOnly={readOnly}
              value={user.name}
              onChange={(e) => setUser({ ...user, name: e.target.value })}
              style={inputStyle}
            />
          </Field>
          <Field label="部门">
            <input
              type="text"
              readOnly={readOnly}
              value={user.department}
              onChange={(e) => setUser({ ...user, department: e.target.value })}
              style={inputStyle}
            />
          </Field>
          <Field label="Role">
            <select
              disabled={readOnly}
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
            <Field label="管的部门（多个用逗号隔开）">
              <input
                type="text"
                value={user.managed_departments.join(", ")}
                onChange={(e) =>
                  setUser({
                    ...user,
                    managed_departments: e.target.value
                      .split(/[,，]\s*/)
                      .filter(Boolean),
                  })
                }
                style={inputStyle}
              />
            </Field>
          )}
          {readOnly && (
            <div style={{ fontSize: 11, color: "var(--status-warn)" }}>
              这个账号已删除，只能看不能改。勾掉列表上的「含已删」就不会再看到他。
            </div>
          )}
          {saveErr && (
            <div style={{ fontSize: 12, color: "var(--status-err)" }}>
              保存失败：{saveErr}
            </div>
          )}
        </div>
      )}
      {confirmClose && (
        <ConfirmDialog
          danger
          title="放弃未保存的改动?"
          confirmLabel="放弃"
          onCancel={() => setConfirmClose(false)}
          onConfirm={() => {
            setConfirmClose(false);
            onClose();
          }}
        >
          刚才改的内容不会保存。
        </ConfirmDialog>
      )}
    </Modal>
  );
}
