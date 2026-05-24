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
import { gatewayGetDevToken, hermesApiConfigGet, hermesApiAuthHeader } from "./tauri";
import { config } from "./env";

// BL-ARCH1 P1 (5/10): 加 sysadmin (catfish-identity 超级管理员).
//   sysadmin > admin > manager > employee, RoleGate 在 web 侧做继承.
//   Companion 这边只用来给 WebPortalLink 决定是否显示 "🔐 系统管理" 锚点.
export type Role = "sysadmin" | "admin" | "manager" | "employee";

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

// BL-FIX-STALE-TOKEN-CACHE (5/24 鸿波"为啥要这样强制才正常"): 删 _cachedEnvToken
// 模块级缓存. 原 bug: Companion 启动早期 invoke('auth_get_access_token') 偶发返
// null (catfish-identity 没起好 / OAuth 还没完成), getToken() 落到 env 兜底链拿
// dev-token-local 并**永久缓存**. 之后即便 silent refresh 把 OAuth 续好, 只要某
// 次 invoke 返 null 就立即回到这个老 cache → gateway 持续 401 → 必须 killall
// Companion 才能恢复. 真没必要 cache — gatewayGetDevToken 是 Tauri IPC ~1ms,
// 每次拉一下不疼.

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
 *   2. .env CATFISH_DEV_TOKEN (开发兜底, 没登录时, **每次现拉**, 不缓存)
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
  // 2. .env 兜底 — BL-FIX-STALE-TOKEN-CACHE (5/24): 不缓存, 每次现拉. 防 Companion
  //    启动早期偶发把 dev-token-local 缓死, 之后 OAuth 续上了 JS 还在用老 cache.
  try {
    return await gatewayGetDevToken();
  } catch {
    return "dev-token-local";
  }
}

/**
 * BL-FIX45 A+ (5/11): 统一 fetch wrapper, 401 自动 reauth + silent retry.
 *
 * 鸿波 5/11 截图: 仪表盘"今日话题"显示"拉不到话题, 看 gateway 起没起". 真因是
 * /api/proactive/starter 返 401 (OAuth token 过期), Companion 没自动 reauth.
 *
 * 原 BL-FIX45 A 只改 chat.ts inline 处理 401. 但 me.ts 里 fetchMe / fetchProactiveStarter /
 * fetchQuota / fetchDevUsers 等十几条 inline fetch 各自都没处理 401.
 *
 * 这个 wrapper 把"加 Authorization header + 检测 401 + 自动 reauth + 重发"一次封装,
 * 所有调 gateway 的 API 都改走它. 不再每条 inline 复制粘贴.
 *
 * 用法:
 *   const resp = await fetchWithAuth(url, { method: "POST", body: ... });
 *   if (!resp.ok) ...
 *
 * 行为:
 *   - 自动加 Authorization: Bearer <token>
 *   - 收 401 → 调 tauri auth_login (浏览器 OAuth flow) → 拿新 token → 重发一次
 *   - retry 上限 1 (防死循环, IdP 挂时还会失败但不无限循环)
 *   - 其它错码 (200/404/500/etc) 原样返, caller 自己处理
 *
 * 不处理 (调用方自己):
 *   - 5xx 上游错 (chat.ts BL-FIX45 B 有 fallback 逻辑, 其它 API 看情况)
 *   - 429 quota (各 caller 有 friendly msg)
 *   - 网络断 (caller catch)
 */
export async function fetchWithAuth(
  input: RequestInfo | URL,
  init?: RequestInit,
  opts: { skipReauth?: boolean } = {},
): Promise<Response> {
  // BL-AUTH-DECOUPLE-A5 Phase 2 (5/19): hermes 路径走静态 API_SERVER_KEY +
  // X-Catfish-User 透传 user identity. 不再 OAuth 1h JWT (那是给 catfish-gateway
  // 老路径用的). hermes proxy 拿 service token 替换 Authorization 调下游 gateway,
  // identity 从 X-Catfish-User 读. 详见 BL-AUTH-DECOUPLE-A1/A2/A3 + Phase 1 总览.
  //
  // 灰度: config.useHermes=false 时仍走老 OAuth 路径 (backward compat + 安全降级).
  if (config.useHermes && config.hermesAuthHeader) {
    return fetchWithHermes(input, init);
  }
  return fetchWithOAuth(input, init, opts);
}

/** BL-AUTH-DECOUPLE-A5 (5/19): hermes 静态 key 路径. 不 reauth (key 不会过期).
 *
 * hermes API_SERVER_KEY 是常驻 static token, 401 没 reauth 必要; 真 401 说明 hermes
 * 自己挂了 / key 错配, 让 caller 看 raw 错码自己决定 (大部分 caller 现在 friendly msg).
 */
async function fetchWithHermes(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const headers = new Headers(init?.headers || {});
  headers.set("Authorization", config.hermesAuthHeader!);
  // X-Catfish-User: 真员工 email. hermes proxy 据此把请求路给下游 gateway,
  // gateway 用这个 email 取 user (BL-AUTH-DECOUPLE-A1 service token + X-Catfish-User
  // 约定). 拿不到 (未登录 / IdP 挂) 不带, 让 hermes 拒 401 — 不静默走错 user.
  try {
    const email = await getCurrentUserEmail();
    if (email) headers.set("X-Catfish-User", email);
  } catch {
    /* keychain 没 token → 不带 header, hermes 401, caller 触发登录 */
  }
  return fetch(input, { ...init, headers });
}

/** BL-AUTH-DECOUPLE-A5 (5/19): 老 catfish-gateway 直调路径 (灰度回退). 保留旧 reauth 行为. */
async function fetchWithOAuth(
  input: RequestInfo | URL,
  init?: RequestInit,
  opts: { skipReauth?: boolean } = {},
): Promise<Response> {
  const doRequest = async (token: string): Promise<Response> => {
    const headers = new Headers(init?.headers || {});
    headers.set("Authorization", `Bearer ${token}`);
    return fetch(input, { ...init, headers });
  };

  let token = await getToken();
  let resp = await doRequest(token);

  if (resp.status === 401 && !opts.skipReauth) {
    // BL-FIX-STALE-TOKEN-CACHE (5/24): 撞 401 第一时间 invalidate user email cache.
    // 老 bug: 启动早期 _cachedUserEmail 可能存了错 email (whoami 半成功 / 刚登录中),
    // 之后 401 reauth 流程拿不到正确 email 路 hermes 头, 同样 401 死循环.
    // 现在 401 时无脑清 cache, 下次 getCurrentUserEmail 重新 whoami.
    _cachedUserEmail = null;
    // 401 → 触发 OAuth re-auth (弹浏览器)
    try {
      await invoke("auth_login");
      // 5/18 BL-COMPANION-AUTO-RELOGIN: 通知 useAuth 刷新 — 不然 LoginGate /
      // AuthBanner / DevUserSwitcher 的 state 还停在过期那一刻, 显错信息.
      // 用 window event 而不是直接调 useAuth refresh 是因为 me.ts 是普通 module,
      // 不在 React tree 里, 拿不到 hook. useAuth 自己挂 listener (下次改).
      try {
        window.dispatchEvent(new CustomEvent("catfish:auth-refreshed"));
      } catch {
        // 不支持 CustomEvent 的极老环境 (不太可能在 Tauri webview), silent
      }
    } catch {
      // auth_login 失败 (用户关浏览器 / IdP 不可达) → 原 401 透传给 caller
      return resp;
    }
    // 拿新 token 重发一次, 不再 retry (防死循环)
    token = await getToken();
    resp = await doRequest(token);
  }

  return resp;
}

/** BL-AUTH-DECOUPLE-A5 (5/19): 读当前登录员工 email — hermes 路径必须传 X-Catfish-User.
 *
 * 走 auth_whoami Tauri 命令读 keychain OAuth token 解出来的 email (一次 IPC ~1ms).
 * 缓存到 memory (单 process 单员工, 切账号要重启). 不缓 localStorage — 避免脏 cache.
 */
let _cachedUserEmail: string | null = null;
async function getCurrentUserEmail(): Promise<string | null> {
  if (_cachedUserEmail) return _cachedUserEmail;
  try {
    const who = await invoke<{ authenticated: boolean; email?: string | null }>(
      "auth_whoami",
    );
    if (who.authenticated && who.email) {
      _cachedUserEmail = who.email;
      return who.email;
    }
  } catch {
    /* Tauri 命令不可用 / 没登录 → null */
  }
  return null;
}

/** 测试钩子 — 单测重置 email cache. 生产代码不调. */
export function _resetUserEmailCacheForTest(): void {
  _cachedUserEmail = null;
}

export async function fetchMe(): Promise<MeInfo> {
  // BL-FIX45 A+ (5/11): 走 fetchWithAuth, 401 自动 reauth.
  // BL-AUTH-DECOUPLE-A5 (5/19): backendUrl 路径, hermes proxy 转 gateway.
  const url = `${config.backendUrl}/api/me`;
  const resp = await fetchWithAuth(url);
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
  // BL-FIX45 A+ (5/11): 走 fetchWithAuth, 401 自动 reauth.
  const url = `${config.backendUrl}/api/quota/department/${encodeURIComponent(dept)}`;
  const resp = await fetchWithAuth(url);
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
  // BL-FIX45 A+ (5/11): 走 fetchWithAuth, 401 自动 reauth.
  const url = `${config.backendUrl}/api/audit/department/${encodeURIComponent(dept)}`;
  const resp = await fetchWithAuth(url);
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
    // 5/23 BL-FETCH-DEV-USERS-AUTH (鸿波): 老代码裸 fetch 不带 Authorization,
    // hermes proxy 强制 Bearer API_SERVER_KEY → 直接 401, 不到 catfish-gateway 那步.
    // 老注释说 "401 时降级 null 已被 try-catch 兜住" — 功能 OK 但 console 一直打
    // 红色 "Failed to load resource: 401" 误导员工以为有 bug. 修法: 跟 chat.ts
    // 同款拿 hermes auth header (Bearer API_SERVER_KEY), 401 噪音消失.
    let hermesAuth: string | null = null;
    try {
      const hcfg = await hermesApiConfigGet();
      if (hcfg?.enabled && hcfg?.has_key) {
        hermesAuth = await hermesApiAuthHeader();
      }
    } catch {
      // Tauri 命令挂 / hermes proxy 没启用 → 裸 fetch 走老路径 (有 401 也吃了)
    }

    const url = `${config.backendUrl}/api/dev/users`;
    const headers: Record<string, string> = {};
    if (hermesAuth) headers["Authorization"] = hermesAuth;
    const resp = await fetch(url, { headers });
    if (!resp.ok) return null; // 404 / prod / 仍 401 (走老 gateway 路径无 hermes auth)
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
  // BL-FIX45 A+ (5/11): 走 fetchWithAuth, 401 自动 reauth.
  const url = `${config.backendUrl}/api/quota/department/${encodeURIComponent(dept)}`;
  const resp = await fetchWithAuth(url, {
    method: "PUT",
    headers: {
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
  // BL-FIX45 A+ (5/11): 走 fetchWithAuth, 401 自动 reauth.
  const resp = await fetchWithAuth(`${config.backendUrl}/api/quota/global`);
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
  // BL-FIX45 A+ (5/11): 走 fetchWithAuth, 401 自动 reauth.
  const resp = await fetchWithAuth(`${config.backendUrl}/api/audit/global`);
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
    // BL-FIX45 A+ (5/11): 走 fetchWithAuth — 鸿波截图 '今日话题拉不到' 真因是
    // OAuth token 过期 401, Companion 没自动 reauth. 现在 wrapper 自动 reauth + 重发.
    const url = `${config.backendUrl}/api/proactive/starter`;
    const resp = await fetchWithAuth(url);
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
    // BL-FIX45 A+ (5/11): 走 fetchWithAuth, 401 自动 reauth.
    const url = `${config.backendUrl}/api/proactive/contextual`;
    const ctrl = new AbortController();
    const t = window.setTimeout(() => ctrl.abort(), 5000);
    try {
      const resp = await fetchWithAuth(url, {
        method: "POST",
        headers: {
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
