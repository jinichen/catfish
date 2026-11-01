/** App 里的单位账号登录: OIDC 授权码 + PKCE, 在系统浏览器里输密码, 经 catfish-mobile:// 回到 App。
 *
 * 跟门户 / Companion 同一个 client (catfish-companion), 同一个身份服务。令牌只在 App 里用:
 * id_token 放内存 (给中转站), refresh_token 放钥匙串 (轮换: 每次续期换一个新的, 旧的作废)。
 */
export const REDIRECT_URI = "catfish-mobile://auth/callback";
const CLIENT_ID = "catfish-companion";

export interface Tokens {
  idToken: string;
  /** 秒 (unix) */
  expiresAt: number;
  refreshToken?: string;
}

export interface Discovery {
  authorization_endpoint: string;
  token_endpoint: string;
}

function b64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function randomString(n = 32): string {
  const b = new Uint8Array(n);
  crypto.getRandomValues(b);
  return b64url(b);
}

export async function pkceChallenge(verifier: string): Promise<string> {
  const h = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return b64url(new Uint8Array(h));
}

export async function discover(server: string): Promise<Discovery> {
  const r = await fetch(`${server}/.well-known/openid-configuration`);
  if (!r.ok) throw new Error(`连不上服务器的登录服务 (HTTP ${r.status})`);
  return (await r.json()) as Discovery;
}

export function authorizeUrl(d: Discovery, state: string, challenge: string): string {
  const q = new URLSearchParams({
    client_id: CLIENT_ID,
    redirect_uri: REDIRECT_URI,
    response_type: "code",
    scope: "openid email profile",
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
  });
  return `${d.authorization_endpoint}?${q}`;
}

/** 回调网址 → code (state 对不上就当没收到, 防别的 App 塞一个回调进来) */
export function codeFromCallback(url: string, state: string): string | null {
  if (!url.startsWith(REDIRECT_URI)) return null;
  const q = new URL(url.replace("catfish-mobile://", "https://x/")).searchParams;
  if (q.get("state") !== state) return null;
  return q.get("code");
}

function tokensFrom(body: Record<string, unknown>): Tokens {
  const idToken = String(body.id_token ?? body.access_token ?? "");
  if (!idToken) throw new Error("登录服务没给令牌");
  const ttl = Number(body.expires_in ?? 3600);
  return {
    idToken,
    expiresAt: Math.floor(Date.now() / 1000) + (Number.isFinite(ttl) ? ttl : 3600),
    refreshToken: typeof body.refresh_token === "string" ? body.refresh_token : undefined,
  };
}

async function postForm(url: string, form: Record<string, string>): Promise<Record<string, unknown>> {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(form).toString(),
  });
  const body = (await r.json().catch(() => ({}))) as Record<string, unknown>;
  if (!r.ok) throw new Error(String(body.error_description ?? body.error ?? `HTTP ${r.status}`));
  return body;
}

export async function exchangeCode(d: Discovery, code: string, verifier: string): Promise<Tokens> {
  return tokensFrom(await postForm(d.token_endpoint, {
    grant_type: "authorization_code", code, redirect_uri: REDIRECT_URI, client_id: CLIENT_ID, code_verifier: verifier,
  }));
}

export async function refresh(d: Discovery, refreshToken: string): Promise<Tokens> {
  const t = tokensFrom(await postForm(d.token_endpoint, {
    grant_type: "refresh_token", refresh_token: refreshToken, client_id: CLIENT_ID,
  }));
  return { ...t, refreshToken: t.refreshToken ?? refreshToken };
}
