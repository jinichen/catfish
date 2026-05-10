/** Admin 用户管理 API 客户端 (BL-ARCH1 P1 5/10).
 *
 * 走 gateway /api/admin/* → identity-server :8998 admin_router.
 * gateway 端 OIDC 验签 + 注入 X-Catfish-User-Role, identity 端 require_admin_or_above.
 */

import { api } from "./api";

export type Role = "sysadmin" | "admin" | "manager" | "employee";

export interface UserBrief {
  email: string;
  name: string;
  department: string;
  role: Role;
  managed_departments: string[];
  locked: boolean;
  locked_at: string | null;
  deleted_at: string | null;
  created_at: string | null;
  last_login_at: string | null;
  must_change_password: boolean;
}

export interface UsersListResponse {
  users: UserBrief[];
  count: number;
  caller_role: Role;
}

export interface MeAsAdmin {
  sub: string;
  dept: string;
  role: Role;
  is_sysadmin: boolean;
  permissions: {
    list_users: boolean;
    create_admin: boolean;
    manage_sysadmin: boolean;
  };
}

export interface CreateUserReq {
  email: string;
  password: string;
  name?: string;
  department?: string;
  role?: Role;
  managed_departments?: string[];
  must_change_password?: boolean;
}

export interface UpdateUserReq {
  name?: string;
  department?: string;
  role?: Role;
  managed_departments?: string[];
}

export interface UsersAuditEvent {
  ts_ms: number;
  action: string;
  target_email: string;
  by_email: string;
  meta: Record<string, unknown>;
}

export const adminApi = {
  meAsAdmin: () => api.get<MeAsAdmin>("/api/admin/me-as-admin"),

  listUsers: (opts?: {
    include_deleted?: boolean;
    department?: string;
    role?: Role;
  }) => {
    const params = new URLSearchParams();
    if (opts?.include_deleted) params.set("include_deleted", "true");
    if (opts?.department) params.set("department", opts.department);
    if (opts?.role) params.set("role", opts.role);
    const q = params.toString();
    return api.get<UsersListResponse>(`/api/admin/users${q ? "?" + q : ""}`);
  },

  getUser: (email: string) =>
    api.get<UserBrief>(`/api/admin/users/${encodeURIComponent(email)}`),

  createUser: (req: CreateUserReq) =>
    api.post<{ ok: boolean; user: UserBrief }>("/api/admin/users", req),

  updateUser: (email: string, req: UpdateUserReq) =>
    api.put<{ ok: boolean; user: UserBrief }>(
      `/api/admin/users/${encodeURIComponent(email)}`,
      req,
    ),

  deleteUser: (email: string) =>
    api.delete<{ ok: boolean }>(`/api/admin/users/${encodeURIComponent(email)}`),

  lockUser: (email: string, locked: boolean) =>
    api.post<{ ok: boolean }>(
      `/api/admin/users/${encodeURIComponent(email)}/lock`,
      { locked },
    ),

  resetPassword: (email: string, new_password: string, force_change = true) =>
    api.post<{ ok: boolean }>(
      `/api/admin/users/${encodeURIComponent(email)}/reset-password`,
      { new_password, force_change },
    ),

  listAudit: (limit: number = 100) =>
    api.get<{ events: UsersAuditEvent[]; limit: number }>(
      `/api/admin/users-audit?limit=${limit}`,
    ),
};
