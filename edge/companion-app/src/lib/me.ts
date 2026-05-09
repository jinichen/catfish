/** Current user info — 五一 sprint 5/2 RBAC + 多账号切换.
 *
 * GET /api/me 返当前 user 元信息, Companion 用来按角色 conditional render Dashboard.
 *
 * role 规则:
 *   - employee: 看自己 quota / audit
 *   - manager:  +看 managed_departments 列表里的部门 quota / audit
 *   - admin:    全权
 *
 * 多账号切换 (五一 5/2 加):
 *   - GET /api/dev/users 列出 dev_users.yaml 里所有测试账号
 *   - DevUserSwitcher 让用户选, 选中的 token 存 localStorage
 *   - getOverrideToken() 读 localStorage, 有就返, 没有 fallback 到 .env dev token
 */

import { invoke } from "@tauri-apps/api/core";
import { gatewayGetDevToken } from "./tauri";
import { config } from "./env";

export type Role = "admin" | "manager" | "employee";

export interface MeInfo {
  email: string;
  department: string;
  role: Role;
  managed_departments: string[];
  auth_method: string;
}

const _DEV_USER_STORAGE_KEY = "catfish:dev_user_token";

/** 当前选中的 dev token override (localStorage). 没选 → null, 走 .env 兜底. */
export function getOverrideToken(): string | null {
  if (typeof localStorage === "undefined") return null;
  try {
    const t = localStorage.getItem(_DEV_USER_STORAGE_KEY);
    return t && t.trim() ? t : null;
  } catch {
    return null;
  }
}

export function setOverrideToken(token: string | null): void {
  if (typeof localStorage === "undefined") return;
  try {
    if (token) localStorage.setItem(_DEV_USER_STORAGE_KEY, token);
    else localStorage.removeItem(_DEV_USER_STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

let _cachedEnvToken: string | null = null;

/** BL-D3 Phase 3.1 (5/9): export 给 McpRegistryCard 等其他卡复用.
 *
 * BL-FIX26 (5/9 鸿波诊断): 优先级修 — OAuth access_token 必须最优先!
 * 之前不读 OAuth, OIDC 登录的员工 (chenhongbo@ffcs.cn) Companion 还是
 * 用 dev_token 调 gateway, gateway 解 dev_token → 'dev-user@catfish.dev'
 * (yaml 虚构 user), 写 quota / audit / chat 全到虚构 user 名下, 真员工
 * Dashboard 永远 0. PG 真证: users 表 [chenhongbo, demo], quota_events
 * 501 行全是 dev-user.
 *
 * 优先级 (5/9 改):
 *   0. OAuth keychain access_token (登录员工的真 token, 最优先)
 *   1. localStorage 切换器 override (dev 调试用)
 *   2. .env CATFISH_DEV_TOKEN (开发兜底, 没登录时)
 *   3. fallback 'dev-token-local' (一切都失败时)
 */
export async function getToken(): Promise<string> {
  // 0. OAuth 登录的真 access_token (BL-FIX26 5/9)
  try {
    const oauth = await invoke<string | null>("auth_get_access_token");
    if (oauth) return oauth;
  } catch {
    // Tauri command 不可用 (Web mode dev) → fallback
  }
  // 1. 切换器选的覆盖
  const override = getOverrideToken();
  if (override) return override;
  // 2. .env 兜底
  if (_cachedEnvToken) return _cachedEnvToken;
  try {
    const t = await gatewayGetDevToken();
    _cachedEnvToken = t;
    return t;
  } catch {
    return "dev-token-local";
  }
}

export async function fetchMe(): Promise<MeInfo> {
  const token = await getToken();
  const url = `${config.gatewayUrl}/api/me`;
  const resp = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return (await resp.json()) as MeInfo;
}

export interface DepartmentQuota {
  department: string;
  day: { used: number; limit: number };
  top_users: { user_email: string; tokens_used: number }[];
  viewer_role: Role;
}

export async function fetchDepartmentQuota(dept: string): Promise<DepartmentQuota> {
  const token = await getToken();
  const url = `${config.gatewayUrl}/api/quota/department/${encodeURIComponent(dept)}`;
  const resp = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return (await resp.json()) as DepartmentQuota;
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
  const token = await getToken();
  const url = `${config.gatewayUrl}/api/audit/department/${encodeURIComponent(dept)}`;
  const resp = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return (await resp.json()) as DepartmentAudit;
}

// ── 多账号切换器 (dev only) ──────────────────────────────────

export interface DevUser {
  email: string;
  name: string;
  department: string;
  role: Role;
  managed_departments: string[];
  token: string; // dev 模式才暴露 (生产 prod 这端点 404)
}

/** 列 dev_users.yaml 配置的所有测试账号. 生产模式 (prod) 端点 404, 切换器隐藏. */
export async function fetchDevUsers(): Promise<DevUser[] | null> {
  try {
    const url = `${config.gatewayUrl}/api/dev/users`;
    const resp = await fetch(url);
    if (!resp.ok) return null; // 404 / prod
    const data = (await resp.json()) as { users: DevUser[] };
    return data.users || [];
  } catch {
    return null;
  }
}

// ── Manager: 改部门 quota (BL-D8 RBAC manager 第二轮) ────────


export async function updateDepartmentQuota(
  dept: string,
  tokensPerDay: number,
): Promise<{ ok: boolean; tokens_per_day?: number; detail?: string }> {
  // BL-FIX35 (5/10): 复用统一 getToken (OAuth keychain 优先, dev_token 兜底).
  // 老 inline 各自 copy-paste, 漏 OAuth → 配额/audit/proactive 卡都 401.
  const token = await getToken();
  const url = `${config.gatewayUrl}/api/quota/department/${encodeURIComponent(dept)}`;
  const resp = await fetch(url, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ tokens_per_day: tokensPerDay }),
  });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const j = await resp.json();
      detail = j.detail || detail;
    } catch {
      /* ignore */
    }
    return { ok: false, detail };
  }
  const j = await resp.json();
  return { ok: true, tokens_per_day: j.tokens_per_day };
}


// ── Admin: 全局聚合 (BL-D8 RBAC admin 视图) ─────────────────


export interface GlobalQuota {
  since_ms: number;
  top_departments: { department: string; request_count: number; tokens_used: number }[];
  viewer_role: Role;
}


export async function fetchGlobalQuota(): Promise<GlobalQuota | null> {
  // BL-FIX35 (5/10): 复用统一 getToken (OAuth keychain 优先, dev_token 兜底).
  // 老 inline 各自 copy-paste, 漏 OAuth → 配额/audit/proactive 卡都 401.
  const token = await getToken();
  const resp = await fetch(`${config.gatewayUrl}/api/quota/global`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) return null;
  return (await resp.json()) as GlobalQuota;
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
  // BL-FIX35 (5/10): 复用统一 getToken (OAuth keychain 优先, dev_token 兜底).
  // 老 inline 各自 copy-paste, 漏 OAuth → 配额/audit/proactive 卡都 401.
  const token = await getToken();
  const resp = await fetch(`${config.gatewayUrl}/api/audit/global`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) return null;
  return (await resp.json()) as GlobalAudit;
}


// ── 主动闲聊 (BL-E13 C-MVP) ──────────────────────────────────

export interface ProactiveStarter {
  starter: string;
  context_hint: string;
  source: "llm" | "fallback";
}

/** 拉一个上下文感知的 starter. gateway 用 journal + 时段 + LLM 生成. */
export async function fetchProactiveStarter(): Promise<ProactiveStarter | null> {
  try {
    const token = await (async () => {
      const o = getOverrideToken();
      if (o) return o;
      try {
        return await gatewayGetDevToken();
      } catch {
        return "dev-token-local";
      }
    })();
    const url = `${config.gatewayUrl}/api/proactive/starter`;
    const resp = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
    if (!resp.ok) return null;
    return (await resp.json()) as ProactiveStarter;
  } catch {
    return null;
  }
}

/** 5/6 BL-E13.5 真主动 Phase B: 信号触发的针对性 starter.
 *
 * signal_kind: 'silence' | 'deadline' | 'focus'
 * context: 各 signal 类型对应字段, 跟 gateway proactive._SIGNAL_KIND_PROMPTS 对齐
 *
 * 5s timeout — 信号触发不能等太久, 超时 / gateway 挂 → 返 null,
 * caller (useProactiveTriggers) 用本地模板兜底.
 */
export async function fetchContextualStarter(
  signalKind: "silence" | "deadline" | "focus",
  context: Record<string, unknown>,
): Promise<ProactiveStarter | null> {
  try {
    const token = await (async () => {
      const o = getOverrideToken();
      if (o) return o;
      try {
        return await gatewayGetDevToken();
      } catch {
        return "dev-token-local";
      }
    })();
    const url = `${config.gatewayUrl}/api/proactive/contextual`;
    const ctrl = new AbortController();
    const t = window.setTimeout(() => ctrl.abort(), 5000);
    try {
      const resp = await fetch(url, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ signal_kind: signalKind, context }),
        signal: ctrl.signal,
      });
      if (!resp.ok) return null;
      const data = (await resp.json()) as ProactiveStarter;
      // gateway 返 source=fallback + starter 空 — 让 caller 用本地模板
      if (!data.starter) return null;
      return data;
    } finally {
      window.clearTimeout(t);
    }
  } catch {
    return null;
  }
}
