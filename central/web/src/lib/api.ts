/** Gateway API 客户端 (BL-ARCH1 5/10).
 *
 * 浏览器 fetch 包装. 自动加 Bearer id_token (跟 Companion BL-FIX31 同设计).
 * 401 自动跳登录页, 把当前 URL 写 state 返回.
 *
 * 跟 Companion lib/me.ts 区别:
 *   - 浏览器侧没 Tauri invoke, token 走 oidc-client-ts userManager.getUser()
 *   - dev 走 vite proxy '/v1/*', prod 走 nginx 反代, 都不写 host
 *   - 401 处理: Companion 走 LoginGate, web 走 redirect_uri
 */

import { getIdToken, login } from "./auth";

export class HttpError extends Error {
  constructor(public status: number, public body: string, message?: string) {
    super(message || `HTTP ${status}: ${body.slice(0, 200)}`);
    this.name = "HttpError";
  }
}

interface FetchOpts {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  /** 401 时是否自动跳登录页 (默认 true). 显式传 false 让 caller 处理. */
  redirectOn401?: boolean;
}

async function request<T>(path: string, opts: FetchOpts = {}): Promise<T> {
  const token = await getIdToken();

  const headers: Record<string, string> = {
    "Accept": "application/json",
    ...opts.headers,
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  if (opts.body !== undefined && !(opts.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }

  const body =
    opts.body instanceof FormData
      ? opts.body
      : opts.body !== undefined
      ? JSON.stringify(opts.body)
      : undefined;

  const resp = await fetch(path, {
    method: opts.method || "GET",
    headers,
    body,
  });

  if (resp.status === 401 && (opts.redirectOn401 ?? true)) {
    // 没登录 / token 过期 → 跳 IdP, 登录回来回原 URL
    await login();
    // 不会走到这, login 会 redirect. 但 TS 需要 throw.
    throw new HttpError(401, "Redirecting to login");
  }

  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new HttpError(resp.status, text);
  }

  // 非 JSON (例 download) 让 caller 自己处理
  const ct = resp.headers.get("content-type") || "";
  if (!ct.includes("application/json")) {
    return (await resp.text()) as unknown as T;
  }
  return (await resp.json()) as T;
}

export const api = {
  get: <T>(path: string, opts?: FetchOpts) =>
    request<T>(path, { ...opts, method: "GET" }),
  post: <T>(path: string, body?: unknown, opts?: FetchOpts) =>
    request<T>(path, { ...opts, method: "POST", body }),
  put: <T>(path: string, body?: unknown, opts?: FetchOpts) =>
    request<T>(path, { ...opts, method: "PUT", body }),
  delete: <T>(path: string, opts?: FetchOpts) =>
    request<T>(path, { ...opts, method: "DELETE" }),
  // BL-Q3-FACT (5/10): 文件上传走 multipart/form-data, body 直接给 FormData
  // request() 已经识别 FormData 跳过 JSON.stringify (line 44-46).
  postFormData: <T>(path: string, formData: FormData, opts?: FetchOpts) =>
    request<T>(path, { ...opts, method: "POST", body: formData }),
};
