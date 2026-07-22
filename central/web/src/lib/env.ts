/** catfish-web 运行时配置 (BL-ARCH1 5/10 · P3.5.79+ 7/22 runtime config 重构).
 *
 * 跟 Companion lib/env.ts 同模式. dev 走 vite proxy (/api /v1), prod 走 nginx
 * 反代同路径. 浏览器侧不需要 gateway URL — fetch('/v1/hub/skills') 即可.
 *
 * OIDC issuer 必须显式配 — 浏览器 PKCE flow 跳哪个 IdP, 跟 Companion oauth.rs
 * 读 ~/.catfish/companion.yaml issuer 同字段.
 *
 * ── 优先级 (P3.5.79+ 7/22 达华 POC catch) ──
 *   1. window.__CATFISH_CONFIG__ (runtime · docker entrypoint 生成 /config.js)
 *   2. import.meta.env.VITE_* (build-time · vite dev / preview 用)
 *   3. 硬编 fallback "http://127.0.0.1:8998" (最后兜底 · 只 dev 生效)
 *
 * 客户装机: 改 .env CATFISH_OIDC_ISSUER=http://<server-ip>:8998 → docker compose
 * restart web · 秒生效 · 不需重编 image. 老 build-arg 方案已废 (每客户 IP 一变
 * 就重编 = 不可 scale · image tar 也无法"一份到处装").
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

// runtime config · docker-entrypoint-catfish-config.sh 从容器 env 生成 /config.js
// index.html 里 <script src="/config.js"> 在 main bundle 前加载 · 挂到 window.
declare global {
  interface Window {
    __CATFISH_CONFIG__?: {
      oidcIssuer?: string;
      oidcClientId?: string;
      oidcScope?: string;
    };
  }
}

const runtimeCfg: Window["__CATFISH_CONFIG__"] =
  (typeof window !== "undefined" ? window.__CATFISH_CONFIG__ : undefined) || {};

// vite env (build-time) — VITE_* 前缀才暴露到浏览器 · dev / preview fallback
const env = import.meta.env;

export const config: RuntimeConfig = {
  oidcIssuer:
    runtimeCfg.oidcIssuer || env.VITE_OIDC_ISSUER || "http://127.0.0.1:8998",
  oidcClientId:
    runtimeCfg.oidcClientId || env.VITE_OIDC_CLIENT_ID || "catfish-companion",
  oidcScope:
    runtimeCfg.oidcScope || env.VITE_OIDC_SCOPE || "openid email profile",
  redirectUri: `${window.location.origin}/auth/callback`,
};

export const isDev = import.meta.env.DEV;
