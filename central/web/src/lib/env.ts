/** catfish-web 运行时配置 (BL-ARCH1 5/10).
 *
 * 跟 Companion lib/env.ts 同模式. dev 走 vite proxy (/api /v1), prod 走 nginx
 * 反代同路径. 浏览器侧不需要 gateway URL — fetch('/v1/hub/skills') 即可.
 *
 * OIDC issuer 必须显式配 — 浏览器 PKCE flow 跳哪个 IdP, 跟 Companion oauth.rs
 * 读 ~/.catfish/companion.yaml issuer 同字段.
 */

interface RuntimeConfig {
  /** OIDC issuer URL (例 'http://127.0.0.1:8998', 生产 'https://sso.client.com') */
  oidcIssuer: string;
  /** OIDC client id, 跟 Companion 一致 — gateway 验签 audience 用 */
  oidcClientId: string;
  /** OAuth scope */
  oidcScope: string;
  /** redirect_uri, 浏览器 PKCE callback 路径 (本 web 自己的 /auth/callback) */
  redirectUri: string;
}

// vite env (build-time) — VITE_* 前缀才暴露到浏览器
const env = import.meta.env;

export const config: RuntimeConfig = {
  oidcIssuer: env.VITE_OIDC_ISSUER || "http://127.0.0.1:8998",
  oidcClientId: env.VITE_OIDC_CLIENT_ID || "catfish-companion",
  oidcScope: env.VITE_OIDC_SCOPE || "openid email profile",
  redirectUri: `${window.location.origin}/auth/callback`,
};

export const isDev = import.meta.env.DEV;
