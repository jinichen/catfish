/** 按 role 守卫页面 (BL-ARCH1 5/10).
 *
 * <RoleGate require="admin"> 子内容只 admin 看到, 其他 role 看 403.
 */

import type { ReactNode } from "react";

import { useAuthStore } from "../store/auth";
import type { Role } from "../lib/me";

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

  // BL-ARCH1 P1 (5/10) 权限继承: sysadmin > admin > manager > employee
  // sysadmin 通过 admin / manager 守卫, admin 通过 manager 守卫.
  const role = me.role;
  const ok =
    allowed.includes(role) ||
    (allowed.includes("admin") && role === "sysadmin") ||
    (allowed.includes("manager") && (role === "admin" || role === "sysadmin"));

  if (!ok) {
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
