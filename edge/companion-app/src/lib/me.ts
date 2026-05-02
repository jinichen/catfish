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

async function getToken(): Promise<string> {
  // 1. 切换器选的覆盖优先
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
