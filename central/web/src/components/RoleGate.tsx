/** 按 role 守卫页面 (BL-ARCH1 5/10).
 *
 * <RoleGate require="admin"> 子内容只 admin 看到, 其他 role 看 403.
 */

import type { ReactNode } from "react";

import { useAuthStore } from "../store/auth";
import type { Role } from "../lib/me";

/** 某个 role 能不能通过某道守卫.
 *
 * BL-ARCH1 P1 (5/10) 权限继承: sysadmin > admin > manager > employee
 * sysadmin 通过 admin / manager 守卫, admin 通过 manager 守卫.
 *
 * 7/30 从 RoleGate 里抽出来导出 —— 侧栏导航要按同一套规则决定显不显示某一项。
 * 各写一套的话迟早对不上, 表现是"菜单里有这一项, 点进去 403", 而那种不一致
 * 没有任何东西会拦住。
 */
export function roleAllows(role: Role, require: Role | Role[]): boolean {
  const allowed = Array.isArray(require) ? require : [require];
  return (
    allowed.includes(role) ||
    (allowed.includes("admin") && role === "sysadmin") ||
    (allowed.includes("manager") && (role === "admin" || role === "sysadmin"))
  );
}

export function RoleGate({
  require,
  children,
}: {
  require: Role | Role[];
  children: ReactNode;
}) {
  const me = useAuthStore((s) => s.me);

  if (!me) return null;

  const allowed = Array.isArray(require) ? require : [require];
  const role = me.role;

  if (!roleAllows(role, require)) {
    return (
      <div
        style={{
          padding: "var(--space-4)",
          textAlign: "center",
          color: "var(--text-muted)",
        }}
      >
        <h2>403 — 没权限</h2>
        <p>这页只 {allowed.join(" / ")} 看, 你是 {role}.</p>
      </div>
    );
  }

  return <>{children}</>;
}
