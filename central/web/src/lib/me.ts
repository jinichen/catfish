/** /api/me 等当前员工信息端点 (BL-ARCH1 5/10).
 *
 * 跟 Companion lib/me.ts 对应, 但去掉 Tauri invoke / dev_token fallback —
 * web 强制走 OIDC PKCE, 没登录就跳登录页. 简化很多.
 */

import { api } from "./api";

// BL-ARCH1 P1 (5/10): 加 sysadmin (catfish-identity 超级管理员).
// sysadmin > admin > manager > employee, RoleGate 在 web 侧做继承.
// 跟 lib/admin.ts 的 Role 对齐 (那边是 admin API 自带的, 这边给 me / quota / audit 用).
export type Role = "sysadmin" | "admin" | "manager" | "employee";

export interface MeInfo {
  email: string;
  department: string;
  role: Role;
  managed_departments: string[];
  auth_method: string;
}

export async function fetchMe(): Promise<MeInfo> {
  return api.get<MeInfo>("/api/me");
}

export interface QuotaWindow {
  used: number;
  limit: number; // 0 = 不限
}

export interface QuotaMe {
  user_email: string;
  department: string;
  minute: QuotaWindow;
  day: QuotaWindow;
  department_day: QuotaWindow;
}

export async function fetchQuotaMe(): Promise<QuotaMe> {
  return api.get<QuotaMe>("/api/quota/me");
}

export interface DepartmentQuota {
  department: string;
  day: { used: number; limit: number };
  top_users: { user_email: string; tokens_used: number }[];
  viewer_role: Role;
}

export async function fetchDepartmentQuota(dept: string): Promise<DepartmentQuota> {
  return api.get<DepartmentQuota>(
    `/api/quota/department/${encodeURIComponent(dept)}`,
  );
}

export interface DepartmentAudit {
  department: string;
  since_ms: number;
  request_count: number;
  total_tokens: number;
  by_model: { model: string; count: number; total_tokens: number }[];
  by_user: { user_email: string; count: number; total_tokens: number }[];
  viewer_role: Role;
}

export async function fetchDepartmentAudit(dept: string): Promise<DepartmentAudit> {
  return api.get<DepartmentAudit>(
    `/api/audit/department/${encodeURIComponent(dept)}`,
  );
}

// ── Admin: 全局聚合 ─────────────────────────────────

export interface GlobalQuota {
  since_ms: number;
  top_departments: { department: string; request_count: number; tokens_used: number }[];
  viewer_role: Role;
}

export async function fetchGlobalQuota(): Promise<GlobalQuota | null> {
  try {
    return await api.get<GlobalQuota>("/api/quota/global");
  } catch {
    return null;
  }
}

export interface GlobalAudit {
  since_ms: number;
  request_count: number;
  total_tokens: number;
  active_users: number;
  active_departments: number;
  by_model: { model: string; count: number; total_tokens: number }[];
  by_department: { department: string; count: number; total_tokens: number }[];
  by_user: { user_email: string; department: string; count: number; total_tokens: number }[];
  viewer_role: Role;
}

export async function fetchGlobalAudit(): Promise<GlobalAudit | null> {
  try {
    return await api.get<GlobalAudit>("/api/audit/global");
  } catch {
    return null;
  }
}

// ── Manager: 改部门 quota ───────────────────────────

export async function updateDepartmentQuota(
  dept: string,
  tokensPerDay: number,
): Promise<{ ok: boolean; tokens_per_day?: number; detail?: string }> {
  try {
    return await api.put<{ ok: boolean; tokens_per_day?: number }>(
      `/api/quota/department/${encodeURIComponent(dept)}`,
      { tokens_per_day: tokensPerDay },
    );
  } catch (e) {
    if (e instanceof Error) return { ok: false, detail: e.message };
    return { ok: false, detail: String(e) };
  }
}
