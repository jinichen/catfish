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

// BL-CENTRAL-WEB-PURGE-MEPAGE (5/17 鸿波): QuotaMe + fetchQuotaMe 删 — 中央 web
// /me 整页删, 员工自查配额走桌面 Companion. /api/quota/me 后端 endpoint 仍
// 保留 (Companion 调). DepartmentQuota 给 manager 视角看本部门, 不删.

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

export interface AuditFilter {
  /** BL-AUDIT-UX-P2 (5/17): drill-down filter, 一次只用 1 个 (多 filter 是 P3). */
  model?: string | null;
  dept?: string | null;
  user_email?: string | null;
}

export interface GlobalAudit {
  since_ms: number;
  /** BL-AUDIT-UX-P1 (5/17): 时间窗长度 (h). 24/168/720 = 24h/7d/30d. */
  since_hours: number;
  /** 员工业务 (排除 internal:* loopback) */
  request_count: number;
  total_tokens: number;
  active_users: number;
  active_departments: number;
  by_model: { model: string; count: number; total_tokens: number }[];
  by_department: { department: string; count: number; total_tokens: number }[];
  by_user: { user_email: string; department: string; count: number; total_tokens: number }[];
  viewer_role: Role;
  /** BL-AUDIT-INTERNAL-SPLIT (5/17): gateway 内部循环消耗 (summarizer / proactive
   *  / 5 维 inject 等), 不算员工业务. sysadmin 看透明度. */
  internal_request_count?: number;
  internal_tokens?: number;
  /** BL-AUDIT-UX-P1 (5/17): 上一个等长窗口的对照数字, 前端做 trend ↑12% / ↓8%. */
  previous_request_count?: number;
  previous_total_tokens?: number;
  previous_active_users?: number;
  previous_active_departments?: number;
  /** BL-AUDIT-UX-P2 (5/17): backend echo 当前 filter, 给前端显示 pill chip. */
  filter?: AuditFilter;
}

export async function fetchGlobalAudit(
  sinceHours: number = 24,
  filter: AuditFilter = {},
): Promise<GlobalAudit | null> {
  try {
    const q = new URLSearchParams();
    q.set("since_hours", String(sinceHours));
    if (filter.model) q.set("model", filter.model);
    if (filter.dept) q.set("dept", filter.dept);
    if (filter.user_email) q.set("user_email", filter.user_email);
    return await api.get<GlobalAudit>(`/api/audit/global?${q.toString()}`);
  } catch {
    return null;
  }
}

// ── P3.5.60 (6/22 鸿波 catch "继续完成"): 全公司 LLM perf 聚合 ─
// 跟 GlobalAudit 区别: 那个走 quota_events (无 latency 字段), 这个走
// gateway_audit (有 latency_ms / ttft_ms). web /admin/perf 页用这条.
export interface GlobalPerfByModel {
  model: string;
  count: number;
  error_count: number;
  total_tokens: number;
  p50_ms: number | null;
  p99_ms: number | null;
}

export interface GlobalPerfByDept {
  department: string;
  count: number;
  error_count: number;
  total_tokens: number;
  active_users: number;
  p50_ms: number | null;
  p99_ms: number | null;
}

export interface GlobalPerf {
  since_ms: number;
  since_hours: number;
  request_count: number;
  ok_count: number;
  error_count: number;
  total_tokens: number;
  active_users: number;
  active_departments: number;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  latency_p99_ms: number | null;
  ttft_p50_ms: number | null;
  ttft_p95_ms: number | null;
  by_model: GlobalPerfByModel[];
  by_department: GlobalPerfByDept[];
  source: "pg" | "jsonl" | "none";
  filter?: { model?: string | null; dept?: string | null };
  viewer_role?: Role;
  schema_note?: string;
}

export interface GlobalPerfFilter {
  model?: string | null;
  dept?: string | null;
}

/** P3.5.60.1 (6/22 鸿波 catch "是太慢还是没有数据"): 不再 silent catch null —
 *  返 {data, error} 让 caller 区分 loading / API 错 / 0 数据. 同 pattern 修整个
 *  perf 链, fetchGlobalAudit 老接口不动 (向后兼容). */
export interface FetchResult<T> {
  data: T | null;
  error: { status?: number; message: string } | null;
}

export async function fetchGlobalPerf(
  sinceHours: number = 24,
  filter: GlobalPerfFilter = {},
): Promise<FetchResult<GlobalPerf>> {
  try {
    const q = new URLSearchParams();
    q.set("since_hours", String(sinceHours));
    if (filter.model) q.set("model", filter.model);
    if (filter.dept) q.set("dept", filter.dept);
    const data = await api.get<GlobalPerf>(`/api/audit/global/perf?${q.toString()}`);
    return { data, error: null };
  } catch (e) {
    const err = e as { status?: number; message?: string };
    // 把 HttpError 透传 (404 = endpoint 没注册 / 没重启 gateway, 401 = 鉴权, 5xx = 真挂)
    return {
      data: null,
      error: {
        status: err.status,
        message: err.message || String(e),
      },
    };
  }
}

// ── BL-ADMIN-AUDIT (5/12 鸿波): 逐条 audit 历史 + 4 维筛选 + 分页 ─

export interface AuditEvent {
  ts: number;          // unix 秒
  user: string;
  department: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  latency_ms: number;
  ttft_ms?: number;
  status: string;      // ok / error / interrupted_resumed (BL-HERMES013-4)
  error?: string;
  security_concern?: string;
}

export interface AuditEventsResponse {
  events: AuditEvent[];
  total: number;
  limit: number;
  offset: number;
  since_ms: number;
  filters: { dept: string; user: string; model: string; status: string };
  viewer_role: Role;
}

export interface AuditEventsParams {
  since_ms?: number;
  dept?: string;
  user_filter?: string;
  model?: string;
  status?: string;
  limit?: number;
  offset?: number;
}

export type AuditEventsResult =
  | { ok: true; data: AuditEventsResponse }
  | { ok: false; error: string };

// BL-CENTRAL-WEB-PURGE-USERDATA (5/17 鸿波): 删 fetchSessionsList /
// fetchSessionDetail / fetchSessionsSearch + 类型 SessionRow / SessionDetail /
// SearchMatch / SessionMessage 等. 它们调 gateway `/api/sessions/me/*` 读员工
// 本机 ~/.hermes/state.db, 违反 BL-CENTRAL-EDGE-BOUNDARY. SessionsPage 已废.
// Companion 自己读本地, 不经中央. 类型 / fetch 都不留, 防被新代码再用.


export async function fetchAuditEvents(
  params: AuditEventsParams = {},
): Promise<AuditEventsResult> {
  try {
    const q = new URLSearchParams();
    if (params.since_ms != null) q.set("since_ms", String(params.since_ms));
    if (params.dept) q.set("dept", params.dept);
    if (params.user_filter) q.set("user_filter", params.user_filter);
    if (params.model) q.set("model", params.model);
    if (params.status) q.set("status", params.status);
    if (params.limit != null) q.set("limit", String(params.limit));
    if (params.offset != null) q.set("offset", String(params.offset));
    const qs = q.toString();
    const data = await api.get<AuditEventsResponse>(
      "/api/audit/events" + (qs ? "?" + qs : ""),
    );
    return { ok: true, data };
  } catch (e: any) {
    // 不再静默吞 — 让 UI 能显示"endpoint 不存在 / gateway 没重启"等真原因
    const msg = e?.message ?? String(e);
    return { ok: false, error: msg };
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

// ── P3.5.94 (6/23 鸿波): /v1/catalog 模型清单 — 给 PerfPage / 其他页面下拉用 ─
//
// catalog 是 anon endpoint (build_catalog 真返 — 不暴露 api_base / key),
// 安全可用. 用 id 做 filter value, display_name 做下拉文案.

export interface CatalogModel {
  id: string;
  display_name: string;
  tier?: string;
  recommended_for?: string[];
  context_window?: number;
  cost_tier?: string;
  supports_tool_use?: boolean;
  supports_vision?: boolean;
  api_key_configured?: boolean;
  is_reachable?: boolean | null;
}

export interface CatalogResponse {
  models: CatalogModel[];
  default?: string | null;
  authenticated?: boolean;
}

export async function fetchModelCatalog(): Promise<CatalogModel[]> {
  try {
    const r = await api.get<CatalogResponse>("/v1/catalog");
    return r.models ?? [];
  } catch {
    return [];
  }
}
