/** OIDC 浏览器 PKCE flow (BL-ARCH1 5/10, BL-ARCH2 fix2 5/10 改 localStorage).
 *
 * 跟 Companion oauth.rs 不同:
 *   - Companion 是 desktop, OAuth callback 起本机临时 HTTP server
 *   - web 是浏览器, OAuth callback 是 web 自己的 URL (/auth/callback)
 *   - PKCE 防 code 拦截 (因为浏览器没法保密 client_secret)
 *
 * 库选 oidc-client-ts (官方维护, IETF 标准实现, ~30KB gzipped).
 *
 * Token 存 localStorage (BL-ARCH2 fix2, 5/10 鸿波: "为什么还要再登录一次"):
 *   - 5/10 v1 用 sessionStorage → tab 关就丢, 多 tab 不共享, 每次都要重登
 *   - 改 localStorage → 跨 tab + 重启浏览器仍登录, 直到 token 过期
 *   - XSS 风险: catfish-web 是内部门户 + CSP 严格 + 没 user-generated HTML, 可控
 *   - 退登: 主动 signoutRedirect / removeUser 清空
 *
 * 跟 Companion 共享:
 *   - 同 catfish-identity (issuer http://127.0.0.1:8998 / 客户 SSO)
 *   - 同 client_id 'catfish-companion' (gateway 验 aud 通)
 *   - 同 id_token 用作 gateway API auth (BL-FIX31 catfish 这套设计)
 *   - **不**共享 token storage (Companion 在 Tauri 内部, 浏览器看不到 Tauri 存储).
 *     真 SSO 要靠 IdP cookie session (catfish-identity 第一次浏览器登录后写
 *     cookie, 下次免登). Phase 1 先做 localStorage 持久化, 至少同浏览器免重登.
 */

import {
  UserManager,
  WebStorageStateStore,
  type UserManagerSettings,
  type User as OidcUser,
} from "oidc-client-ts";

import { config } from "./env";

const settings: UserManagerSettings = {
  authority: config.oidcIssuer,
  client_id: config.oidcClientId,
  redirect_uri: config.redirectUri,
  post_logout_redirect_uri: `${window.location.origin}/`,
  response_type: "code",
  scope: config.oidcScope,
  // PKCE 自动启用 (oidc-client-ts 默认)
  loadUserInfo: false,  // gateway /api/me 已经返完整 user info, 不重复调 IdP /userinfo
  monitorSession: false,  // 不需要 IdP iframe 心跳
  // BL-ARCH2 fix2 (5/10): localStorage 持久化 — tab 关 / 浏览器重启都不丢登录态.
  userStore: new WebStorageStateStore({ store: window.localStorage }),
  // PKCE state (signinRedirect 临时存 verifier) 也走 localStorage, 避免新 tab 跳
  // 回 callback 时 sessionStorage 找不到 state.
  stateStore: new WebStorageStateStore({ store: window.localStorage }),
};

export const userManager = new UserManager(settings);

/** 当前已登录用户. 没登录返 null. 启动时 / fetch 401 后调. */
export async function getCurrentUser(): Promise<OidcUser | null> {
  try {
    const user = await userManager.getUser();
    if (!user || user.expired) return null;
    return user;
  } catch (e) {
    console.warn("[auth] getUser 失败:", e);
    return null;
  }
}

/** 跳 IdP 登录页 (PKCE). 当前 URL 存到 state, 登录回来回到这页. */
export async function login(returnTo?: string): Promise<void> {
  const target = returnTo || window.location.pathname + window.location.search;
  await userManager.signinRedirect({ state: { returnTo: target } });
}

/** /auth/callback 处理 — 把 code 换 token, 跳回 returnTo. */
export async function handleCallback(): Promise<string> {
  const user = await userManager.signinRedirectCallback();
  const state = user.state as { returnTo?: string } | undefined;
  return state?.returnTo || "/";
}

/** 退登. 清 localStorage + 跳 IdP logout (catfish-identity 没实现就只本地清). */
export async function logout(): Promise<void> {
  try {
    await userManager.signoutRedirect();
  } catch {
    // catfish-identity Phase 1 没实现 end_session_endpoint, 本地清就行
    await userManager.removeUser();
    window.location.href = "/";
  }
}

/** 拿 id_token (Bearer 给 gateway 用, 跟 Companion BL-FIX31 同设计). */
export async function getIdToken(): Promise<string | null> {
  const user = await getCurrentUser();
  return user?.id_token || null;
}
