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
import {
  hermesApiConfigGet,
  hermesApiAuthHeader,
  fetchProactiveContext,
} from "./tauri";
import { config } from "./env";
// BL-CSP-PROXY (7/18 鸿波): Rust reqwest HTTP 代理 · CSP connect-src 保持严格.
// 前端所有 fetch (fetchWithHermes / fetchWithOAuth doRequest) 走这个, 不直接 fetch().
import { fetchViaProxy } from "./http_proxy";

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
// Companion 才能恢复.
//
// P39 (5/22 gateway 解耦收尾): 删 gatewayGetDevToken 兜底层. 生产员工机由
// launchctl 起 gateway, 不再暴露 dev token. OAuth 未完成时直接兜到
// "dev-token-local" 让 gateway 401 → fetchWithAuth 触发 auth_login 重登.

/** BL-D3 Phase 3.1 (5/9): export 给 McpRegistryCard 等其他卡复用.
 *
 * BL-FIX26 (5/9 鸿波诊断): 优先级修 — OAuth access_token 必须最优先!
 * 之前不读 OAuth, OIDC 登录的员工 (chenhongbo@ffcs.cn) Companion 还是
 * 用 dev_token 调 gateway, gateway 解 dev_token → 'dev-user@catfish.dev'
 * (yaml 虚构 user), 写 quota / audit / chat 全到虚构 user 名下, 真员工
 * Dashboard 永远 0. PG 真证: users 表 [chenhongbo, demo], quota_events
 * 501 行全是 dev-user.
 *
 * 优先级 (P39 5/22 gateway 解耦收尾 改):
 *   0. OAuth keychain access_token (登录员工的真 token, 最优先)
 *   1. localStorage 切换器 override (dev 调试用)
 *   2. fallback 'dev-token-local' (让 gateway 401 → fetchWithAuth 触发 auth_login)
 *
 * 老 gatewayGetDevToken 层 (读 gateway .env dev_token) 已删 — 生产员工机
 * 不该有 gateway 源码目录, 该 command 生产返错, 只让 OAuth 失败时静默污染
 * token cache. 直接兜到 fallback 值, 走 auth_login 正确路径.
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
  // 2. fallback (让 gateway 401 → fetchWithAuth 触发 auth_login 重登)
  return "dev-token-local";
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
  // BL-PLUGIN-P7-PROXY-TOKEN-SWAP (6/1): 之前 me.ts 加了 path 感知补丁
  // (/api/* 强制 OAuth 直连 8999) 绕过 hermes P7 catch-all 的 token swap bug.
  // 真元凶定位在 catfish-xcatfish-user plugin _handle_companion_proxy line 521
  // — 透传 client Bearer 没替换. 改 plugin (6/1 audit), me.ts path 感知补丁
  // 退役. 现在 /api/* /v1/* 全走 hermes, plugin P7 替换 service token 转发.
  //
  // 灰度: config.useHermes=false 时仍走老 OAuth 路径 (backward compat + 安全降级).
  //
  // 6/2 BL-API-PATH-AWARE-AUTH (鸿波 6/1 必须修): 5/31 退役的 path 感知补丁
  // 重新启用. hermes 0.15.1 升级后 P7 catch-all middleware 时机问题, token swap
  // 不生效, /api/* 走 hermes 仍用 client API_SERVER_KEY, gateway 8999 不认 → 401.
  //
  // 真路由:
  //   /v1/chat/completions, /v1/models       → hermes 8642, useHermes path
  //   /api/* (catfish 自己 endpoint)         → gateway 8999 (我们已改 URL),
  //                                            必须走 OAuth path (gateway 要 OIDC JWT)
  //   /v1/catalog                            → gateway 8999, OAuth path (catalog 公开
  //                                            匿名也 OK 但走 OAuth 兼容认证用户场景)
  //
  // 真修 P7 时机 (BL-P7-EARLY-PATCH) 留周一, 修后此 path 感知补丁仍兼容 (gateway 直连
  // 仍合法, 只是不再必要).
  const url = typeof input === "string"
    ? input
    : input instanceof URL ? input.href : input.url;
  // P3.3.37 (6/12 鸿波): /v1/hub/* (SkillsHub) + /v1/wiki/* (WikiHub) 加直连白名单.
  //   gateway 8999 反代 /v1/hub/* → :8997, /v1/wiki/* → :8994, 设计是 Companion
  //   OAuth 直连 gateway, 不该走 hermes proxy 8642 path.
  //   漏配真因: P3.3.18 (wiki, 6/10) + 5/16 SkillsHub 加 fetch URL 时没同步
  //   isGatewayDirectPath 白名单. hermes proxy 对这俩 endpoint 返 500 (实测
  //   curl http://127.0.0.1:8642/v1/hub/skills) — 用户看的 "Load failed" 真因.
  // P3.4.E (6/15 鸿波): catfish_direct=1 query 强走 OAuth 直连 gateway 8999, 不走
  //   hermes 8642 agent loop.
  //   真因: hermes _handle_chat_completions (api_server.py:1820) 把 client request 重 framing
  //   成 agent run (调 _run_agent), **完全不读 client 的 tools / tool_choice 字段** —
  //   client 传啥 tools 都被 hermes 丢弃, 改用 hermes 自己注册的 tools 跑 agent loop.
  //   profile.ts (single-shot) + advisor.ts Call 2 (single-shot 转结构化) 需要 tool_choice
  //   强制 LLM 返结构化, 必须 bypass hermes agent loop 直走 catfish-gateway 8999 (LiteLLM
  //   纯 passthrough, OpenAI 协议透传 DeepSeek).
  //   设计点: 不动 hermes 路由 (那是 hermes 核心), 用 query flag 让客户端能按 endpoint
  //   选择是否走 hermes — caller 显式标 catfish_direct=1 才直走 8999.
  // P3.5.198.b (7/8 鸿波 catch modal "Invalid API key 401"): /api/platforms/*
  // 是 hermes 独占 namespace (hermes gateway/platforms/*.py 平台协议 endpoint,
  // P30 wechat qr_login start/poll 就落这里, 未来 telegram/discord 等如果也走
  // hermes 一样落这个前缀). exclude 掉让它走 fetchWithHermes 用 API_SERVER_KEY,
  // 别错拿 OAuth JWT 撞 hermes _check_auth 死 401. gateway 8999 侧代码 grep 无
  // /api/platforms/ 冲突, exclude 安全.
  //
  // 同时修 UX: 老逻辑 → OAuth 401 → 触发 invoke("auth_login") 弹浏览器登录页,
  // 员工每次点 "微信扫码绑定" 都被踢去 IdP, 那个"要求网页再登录"就是这里.
  const isGatewayDirectPath =
    (url.includes("/api/") && !url.includes("/api/platforms/")) ||
    url.includes("/v1/catalog") ||
    url.includes("/v1/hub/") ||
    url.includes("/v1/wiki/") ||
    url.includes("catfish_direct=1");

  // 6/8 BL-EMPLOYEE-SELF-SERVE A4 ⭐: transparent log middleware. 包 fetchWithAuth
  // 的真实 dispatch (Hermes / OAuth path), 每个 outbound 请求都记本机 SQLite ~/.catfish/outbound_log.db.
  // 让员工**自己审计** catfish 中央服务交换数据是不是符合 "数据零出端" 承诺.
  // manifesto 公理 2 的 enforcement 层 — 不是 "我们承诺" 而是 "你自己看".
  return logOutbound(url, init, async () => {
    if (config.useHermes && config.hermesAuthHeader && !isGatewayDirectPath) {
      return fetchWithHermes(input, init);
    }
    return fetchWithOAuth(input, init, opts);
  });
}

/** 6/8 A4: transparent log middleware. 调真 fetch + 把 metadata 写本机 SQLite.
 *
 * 失败 silent (Tauri command 挂了不该阻塞 chat). 只记 metadata + body preview (4KB
 * cap, Rust 端再 cap), 不记 response body (隐私 + 大). 9 天 GC.
 *
 * 走 dynamic import 防 storybook / dev mode 没 Tauri 时崩 (跟 env.ts 同 pattern).
 */
async function logOutbound(
  url: string,
  init: RequestInit | undefined,
  realFetch: () => Promise<Response>,
): Promise<Response> {
  const method = (init?.method || "GET").toUpperCase();
  const bodyStr =
    typeof init?.body === "string"
      ? init.body
      : init?.body != null
        ? "[binary or non-string body]"
        : undefined;

  let resp: Response | null = null;
  let networkErr: unknown = null;
  try {
    resp = await realFetch();
  } catch (e) {
    networkErr = e;
  }

  // 后台 record (不 await 不阻塞主 fetch)
  void (async () => {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      let respSummary: string | undefined;
      let respBytes: number | undefined;
      if (resp) {
        const ctype = resp.headers.get("content-type") || "";
        respSummary = `${resp.status} ${ctype.split(";")[0] || "unknown"}`;
        const clen = resp.headers.get("content-length");
        if (clen) respBytes = Number(clen);
      }
      await invoke("transparent_log_record", {
        req: {
          method,
          url,
          requestBody: bodyStr,
          status: resp?.status,
          responseBytes: respBytes,
          responseSummary: respSummary,
          error: networkErr ? String(networkErr) : undefined,
        },
      });
    } catch {
      // silent. transparent log 是辅助, 挂了不影响主 fetch.
    }
  })();

  if (networkErr) {
    throw networkErr;
  }
  return resp!;
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
  // BL-CSP-PROXY (7/18): 走 Rust reqwest 代理, CSP 严格. fetchViaProxy auto-detect
  // stream (chat completions 走 event bridge, 其他一次拿完 body).
  const urlStr = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  return fetchViaProxy(urlStr, { ...init, headers });
}

/** P3.5.42.11 (鸿波 6/20 catch '反复弹认证'): auth_login 全局节流.
 *
 * 老 bug: 多个 fetchWithOAuth 并发撞 401, 各自 invoke('auth_login') 弹 N 次浏览器;
 * 离单位 IdP 不可达时 invoke 立即失败, 下次再撞 401 又弹, 反复.
 *
 * 节流策略:
 *   - inflight: auth_login 在跑时, 后续 401 等同一 promise, 不再启第二个
 *   - cooldown: 上次 auth_login 失败 30s 内, 不再 invoke (let 401 透传 silent)
 *     成功的话 cooldown 不生效 (新 token 已写盘, 下次 fetch 该过)
 */
let _authLoginInFlight: Promise<void> | null = null;
let _authLoginLastFailMs = 0;
const _AUTH_LOGIN_FAIL_COOLDOWN_MS = 30_000;

/** BL-AUTH-DECOUPLE-A5 (5/19): 老 catfish-gateway 直调路径 (灰度回退). 保留旧 reauth 行为. */
async function fetchWithOAuth(
  input: RequestInfo | URL,
  init?: RequestInit,
  opts: { skipReauth?: boolean } = {},
): Promise<Response> {
  const doRequest = async (token: string): Promise<Response> => {
    const headers = new Headers(init?.headers || {});
    headers.set("Authorization", `Bearer ${token}`);
    // BL-CSP-PROXY (7/18): 走 Rust reqwest 代理, CSP 严格.
    const urlStr = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    return fetchViaProxy(urlStr, { ...init, headers });
  };

  let token = await getToken();
  let resp = await doRequest(token);

  if (resp.status === 401 && !opts.skipReauth) {
    // BL-FIX-STALE-TOKEN-CACHE (5/24): 撞 401 第一时间 invalidate user email cache.
    // 老 bug: 启动早期 _cachedUserEmail 可能存了错 email (whoami 半成功 / 刚登录中),
    // 之后 401 reauth 流程拿不到正确 email 路 hermes 头, 同样 401 死循环.
    _cachedUserEmail = null;

    // P3.5.42.11: cooldown — 上次 auth_login 失败 30s 内, 不再 invoke
    const now = Date.now();
    if (_authLoginLastFailMs > 0 && now - _authLoginLastFailMs < _AUTH_LOGIN_FAIL_COOLDOWN_MS) {
      // 离单位 IdP 不可达时反复 invoke 没意义, silent 透传 401
      return resp;
    }

    // P3.5.42.11: inflight — 已经在弹了, 等同一 promise 不再启第二个浏览器
    if (_authLoginInFlight) {
      try {
        await _authLoginInFlight;
      } catch {
        return resp;
      }
    } else {
      _authLoginInFlight = (async () => {
        try {
          await invoke("auth_login");
          // 5/18 BL-COMPANION-AUTO-RELOGIN: 通知 useAuth 刷新 — 不然 LoginGate /
          // AuthBanner / DevUserSwitcher 的 state 还停在过期那一刻, 显错信息.
          try {
            window.dispatchEvent(new CustomEvent("catfish:auth-refreshed"));
          } catch {
            // 不支持 CustomEvent 的极老环境 (不太可能在 Tauri webview), silent
          }
          _authLoginLastFailMs = 0;  // 成功 → 清 cooldown
        } catch (e) {
          _authLoginLastFailMs = Date.now();  // 失败 → 开 cooldown
          throw e;
        } finally {
          _authLoginInFlight = null;
        }
      })();
      try {
        await _authLoginInFlight;
      } catch {
        // auth_login 失败 (用户关浏览器 / IdP 不可达) → 原 401 透传给 caller
        return resp;
      }
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
  const url = `${config.gatewayUrl}/api/me`;
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
  const url = `${config.gatewayUrl}/api/quota/department/${encodeURIComponent(dept)}`;
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
  const url = `${config.gatewayUrl}/api/audit/department/${encodeURIComponent(dept)}`;
  const resp = await fetchWithAuth(url);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return (await resp.json()) as DepartmentAudit;
}

// ── BL-EMPLOYEE-PRIVACY-VERIFICATION (#79, 5/25): 员工自查中央存了我啥 ──
//
// /api/audit/me 返本员工的 metadata (无 prompt / response 文本).
// PrivacyCard + privacy-audit CLI 都用这条.

export interface MyAuditSummary {
  user_email: string;
  department: string;
  since_ms: number;
  schema_note: string;
  request_count: number;
  total_tokens: number;
  by_model: { model: string; count: number; total_tokens: number }[];
  first_seen_ts: number | null;
  last_seen_ts: number | null;
  /** 6/2 BL-PRIVACY-CARD-QUOTA-PROGRESS: 员工本日 token 上限. 0 = 不限.
   * PrivacyCard 用来渲染"本月配额 ███░░ 用了 / 上限" 进度条. */
  quota_day_limit: number;
}

export async function fetchMyAudit(): Promise<MyAuditSummary> {
  const url = `${config.gatewayUrl}/api/audit/me`;
  const resp = await fetchWithAuth(url);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return (await resp.json()) as MyAuditSummary;
}

// ── P3.5.59 Phase 2 (6/22 鸿波 catch "把中央端完成"): 单员工 LLM perf 聚合
// 走 gateway_audit 表 (含 latency_ms / ttft_ms), 跟 /api/audit/me 互补.

export interface RemoteLlmPerfSummary {
  user_email: string;
  since_ms: number;
  window_hours: number;
  schema_note: string;
  request_count: number;
  ok_count: number;
  error_count: number;
  total_tokens: number;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  latency_p99_ms: number | null;
  ttft_p50_ms: number | null;
  ttft_p95_ms: number | null;
  by_model: Array<{
    model: string;
    count: number;
    total_tokens: number;
    p50_ms: number | null;
  }>;
  /** 'pg' (生产 PG) | 'jsonl' (dev / 私有部署 fallback) | 'none' (没数据) */
  source: "pg" | "jsonl" | "none";
}

export async function fetchMyLlmPerf(hours: number = 24): Promise<RemoteLlmPerfSummary> {
  const url = `${config.gatewayUrl}/api/audit/me/perf?hours=${hours}`;
  const resp = await fetchWithAuth(url);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return (await resp.json()) as RemoteLlmPerfSummary;
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

    const url = `${config.gatewayUrl}/api/dev/users`;
    const headers: Record<string, string> = {};
    if (hermesAuth) headers["Authorization"] = hermesAuth;
    // BL-CSP-PROXY (7/18): 走 Rust reqwest 代理, CSP 严格.
    const resp = await fetchViaProxy(url, { headers });
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
  const url = `${config.gatewayUrl}/api/quota/department/${encodeURIComponent(dept)}`;
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
  const resp = await fetchWithAuth(`${config.gatewayUrl}/api/quota/global`);
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
  const resp = await fetchWithAuth(`${config.gatewayUrl}/api/audit/global`);
  if (!resp.ok) return null;
  return (await resp.json()) as GlobalAudit;
}


// ── 主动闲聊 (BL-E13 C-MVP) ──────────────────────────────────
//
// BL-PROACTIVE-STARTER-KILL (7/24 鸿波): fetchProactiveStarter 整个函数砍了 —
// Dashboard ProactiveCard 和 useProactiveScheduler 死时间 push 都用它, 两处都
// 因为 3 环 bug 被砍 (gateway 只拿 journal_tail 延迟 + memory sync_turn 节流
// 不同步 + LLM 无历史 dedupe → 员工体感 "刚说过又问"). gateway
// /api/proactive/starter endpoint 无害保留 (后端稳定, 前端无 caller).
//
// ProactiveStarter interface 保留 — fetchContextualStarter (下方) 仍用它做返值
// 类型. contextual 只在 useProactiveTriggers 事件触发 (silence/deadline/focus)
// 时调, 场景不重复不 spam.

export interface ProactiveStarter {
  starter: string;
  context_hint: string;
  source: "llm" | "fallback";
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
    // BL-PROACTIVE-DECOUPLE v2 (5/26 CORS 修): last_model 从 header 挪 body 字段,
    // 跟 /api/proactive/starter 同款 (绕 hermes proxy CORS allowlist).
    let lastModel = "";
    try {
      const ctx = await fetchProactiveContext();
      if (ctx.last_model) lastModel = ctx.last_model;
    } catch (e) {
      console.warn("[proactive contextual] fetchProactiveContext 失败:", e);
    }

    // BL-FIX45 A+ (5/11): 走 fetchWithAuth, 401 自动 reauth.
    const url = `${config.gatewayUrl}/api/proactive/contextual`;
    const ctrl = new AbortController();
    const t = window.setTimeout(() => ctrl.abort(), 5000);
    try {
      const resp = await fetchWithAuth(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          signal_kind: signalKind,
          context,
          last_model: lastModel,  // BL-PROACTIVE-DECOUPLE v2: body 字段, 不再 header
        }),
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
