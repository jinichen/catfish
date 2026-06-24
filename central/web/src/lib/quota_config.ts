/** P3.5.93 (6/23 鸿波): /admin/quota 编辑 UI 后端调用.
 *
 * 10 个 endpoint, sysadmin only. 改 quotas.yaml (gateway hot reload 真生效路径).
 *
 * 跟 lib/me.ts updateDepartmentQuota 区别:
 *   - 那个走 /api/quota/department/{dept} (manager 也能改, 5/2 BL-D9 老路径)
 *   - 这里全 /api/admin/quota/* (sysadmin only, P3.5.93 新)
 *
 * 治本 audit-5 dead UI: 部门 quota 编辑 6 周来分两处 (AccessPage 不生效 +
 * yaml 手动改). 全收口到 QuotaConfigPage 走 yaml.
 */

import { api } from "./api";
import type { Role } from "./me";

/** 单 model 配置 (defaults.per_model.<name>) */
export interface ModelQuotaEntry {
  tokens_per_day: number;
}

/** 单部门配置 (defaults.per_department.<name> 或 overrides.departments.<name>) */
export interface DepartmentQuotaEntry {
  tokens_per_day: number;
}

/** 单用户 override (overrides.users.<email>) */
export interface UserOverrideEntry {
  tokens_per_minute: number;
  tokens_per_day: number;
}

/** 全员默认 (defaults.per_user) */
export interface DefaultPerUser {
  tokens_per_minute: number;
  tokens_per_day: number;
}

/** GET /api/admin/quota/config 完整返值 */
export interface QuotaConfigResponse {
  config: {
    defaults?: {
      per_user?: DefaultPerUser;
      per_model?: Record<string, ModelQuotaEntry>;
      per_department?: Record<string, DepartmentQuotaEntry>;
    };
    overrides?: {
      users?: Record<string, UserOverrideEntry>;
      departments?: Record<string, DepartmentQuotaEntry>;
    };
  };
  viewer_role: Role;
}

export const quotaConfigApi = {
  // ── 读 ────────────────────────────────────────
  get: () => api.get<QuotaConfigResponse>("/api/admin/quota/config"),

  // ── defaults.per_user (全员默认) ───────────────
  putDefaultPerUser: (body: DefaultPerUser) =>
    api.put<{ ok: boolean; tokens_per_minute: number; tokens_per_day: number }>(
      "/api/admin/quota/defaults/per_user",
      body,
    ),

  // ── defaults.per_model.<name> ────────────────
  putPerModel: (name: string, tokens_per_day: number) =>
    api.put<{ ok: boolean; name: string; tokens_per_day: number }>(
      `/api/admin/quota/per_model/${encodeURIComponent(name)}`,
      { tokens_per_day },
    ),
  deletePerModel: (name: string) =>
    api.delete<{ ok: boolean; name: string }>(
      `/api/admin/quota/per_model/${encodeURIComponent(name)}`,
    ),

  // ── defaults.per_department.<name> ───────────
  putPerDepartment: (name: string, tokens_per_day: number) =>
    api.put<{ ok: boolean; name: string; tokens_per_day: number }>(
      `/api/admin/quota/per_department/${encodeURIComponent(name)}`,
      { tokens_per_day },
    ),
  deletePerDepartment: (name: string) =>
    api.delete<{ ok: boolean; name: string }>(
      `/api/admin/quota/per_department/${encodeURIComponent(name)}`,
    ),

  // ── overrides.users.<email> ──────────────────
  putUserOverride: (email: string, body: UserOverrideEntry) =>
    api.put<{ ok: boolean; email: string } & UserOverrideEntry>(
      `/api/admin/quota/overrides/users/${encodeURIComponent(email)}`,
      body,
    ),
  deleteUserOverride: (email: string) =>
    api.delete<{ ok: boolean; email: string }>(
      `/api/admin/quota/overrides/users/${encodeURIComponent(email)}`,
    ),

  // ── overrides.departments.<name> ─────────────
  putDeptOverride: (name: string, tokens_per_day: number) =>
    api.put<{ ok: boolean; name: string; tokens_per_day: number }>(
      `/api/admin/quota/overrides/departments/${encodeURIComponent(name)}`,
      { tokens_per_day },
    ),
  deleteDeptOverride: (name: string) =>
    api.delete<{ ok: boolean; name: string }>(
      `/api/admin/quota/overrides/departments/${encodeURIComponent(name)}`,
    ),
};
