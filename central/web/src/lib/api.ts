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

/** 后端返回体 → 给人看的一句话 (7/30).
 *
 * ## 在补什么
 *
 * 原来是 `body.slice(0, 200)` —— 直接把**原始响应文本**塞进 message。后果:
 *
 *   · 界面上显示的是 JSON 信封: `HTTP 400: {"detail":"模型 X 还被…"}`
 *   · 后端消息里的换行是 JSON 转义的, 显示成字面量 `\n`
 *   · **硬截 200 字符**, 长一点的说明就在半句话中间被切断
 *
 * 7/30 实例: 删模型被护栏拦下时, 后端返的是一段带换行、列出了所有引用者、
 * 并说明下一步该做什么的说明。到界面上变成一坨带 `\n` 的 JSON, 而且正好在
 * "为什么要拦: 链里指向不存在的" 处截断 —— 最关键的那半句没了。
 *
 * 后端把话说清楚了, 却在最后一米被信封和截断毁掉。这一层影响全站 30 处
 * 显示 e.message 的地方, 不是某一页的问题。
 *
 * ## 为什么还留着上限
 *
 * 后端偶尔会返 HTML 错误页 (反代 502 之类), 整页塞进红框没有意义。但改成
 * 显式标注"已截断", 而不是让人以为消息本来就是那样断的。
 */
const _MAX_ERR_CHARS = 2000;

export function describeErrorBody(body: string): string {
  const raw = (body || "").trim();
  if (!raw) return "(响应体为空)";

  let text = raw;
  try {
    const j = JSON.parse(raw);
    // FastAPI 的标准错误形状: {"detail": "..."} 或 {"detail": [{loc,msg,...}]}
    if (typeof j?.detail === "string") {
      text = j.detail;
    } else if (Array.isArray(j?.detail)) {
      // 请求体校验失败时是一个数组, 每项有 loc / msg
      text = j.detail
        .map((d: { loc?: unknown[]; msg?: string }) =>
          d?.loc?.length ? `${d.loc.join(".")}: ${d.msg ?? ""}` : (d?.msg ?? JSON.stringify(d)),
        )
        .join("\n");
    } else if (typeof j?.message === "string") {
      text = j.message;
    }
  } catch {
    // 不是 JSON (HTML 错误页 / 纯文本) —— 原样用
  }

  return text.length > _MAX_ERR_CHARS
    ? `${text.slice(0, _MAX_ERR_CHARS)}\n…（错误信息过长，已截断）`
    : text;
}

export class HttpError extends Error {
  constructor(public status: number, public body: string, message?: string) {
    super(message || `HTTP ${status}: ${describeErrorBody(body)}`);
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
