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
}

export async function fetchGlobalAudit(): Promise<GlobalAudit | null> {
  try {
    return await api.get<GlobalAudit>("/api/audit/global");
  } catch {
    return null;
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

// ── Sessions browse (5/12 借鉴 hermes-desktop A) ────────────────

export interface SessionRow {
  id: string;
  started_at: number;
  started_iso: string;
  message_count: number;
  title: string;
  first_user_msg: string;
}

export interface SessionsListResponse {
  sessions: SessionRow[];
  total: number;
  limit: number;
  offset: number;
  days_back: number;
  q: string;
  viewer: string;
}

export type SessionsListResult =
  | { ok: true; data: SessionsListResponse }
  | { ok: false; error: string };

export interface SessionsListParams {
  days_back?: number;
  q?: string;
  limit?: number;
  offset?: number;
}

export async function fetchSessionsList(
  params: SessionsListParams = {},
): Promise<SessionsListResult> {
  try {
    const q = new URLSearchParams();
    if (params.days_back != null) q.set("days_back", String(params.days_back));
    if (params.q) q.set("q", params.q);
    if (params.limit != null) q.set("limit", String(params.limit));
    if (params.offset != null) q.set("offset", String(params.offset));
    const qs = q.toString();
    const data = await api.get<SessionsListResponse>(
      "/api/sessions/me" + (qs ? "?" + qs : ""),
    );
    return { ok: true, data };
  } catch (e: any) {
    return { ok: false, error: e?.message ?? String(e) };
  }
}

export interface SessionMessage {
  id: number;
  role: string;
  content: string;
  created_at: number;
}

export interface SessionDetail {
  id: string;
  started_at: number;
  started_iso: string;
  message_count: number;
  title: string;
  messages: SessionMessage[];
  messages_returned: number;
  viewer: string;
}

export type SessionDetailResult =
  | { ok: true; data: SessionDetail }
  | { ok: false; error: string };

export async function fetchSessionDetail(
  id: string,
  maxMessages = 500,
): Promise<SessionDetailResult> {
  try {
    const data = await api.get<SessionDetail>(
      `/api/sessions/me/${encodeURIComponent(id)}?max_messages=${maxMessages}`,
    );
    return { ok: true, data };
  } catch (e: any) {
    return { ok: false, error: e?.message ?? String(e) };
  }
}

export interface SearchMatch {
  session_id: string;
  role: string;
  snippet: string;
  created_at: number;
}

export interface SessionsSearchResponse {
  matches: SearchMatch[];
  q: string;
  viewer: string;
}

export type SessionsSearchResult =
  | { ok: true; data: SessionsSearchResponse }
  | { ok: false; error: string };

export async function fetchSessionsSearch(
  q: string,
  daysBack = 30,
  limit = 50,
): Promise<SessionsSearchResult> {
  try {
    const params = new URLSearchParams({
      q,
      days_back: String(daysBack),
      limit: String(limit),
    });
    const data = await api.get<SessionsSearchResponse>(
      "/api/sessions/me/search?" + params.toString(),
    );
    return { ok: true, data };
  } catch (e: any) {
    return { ok: false, error: e?.message ?? String(e) };
  }
}


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
