/** BL-CSP-PROXY (7/18 鸿波): Rust reqwest HTTP 代理 · 让前端 fetch 走 Rust · CSP connect-src 保持严格.
 *
 * 背景: Tauri CSP connect-src 白名单只有 http://127.0.0.1:* + localhost:*. 面板改远端 IP
 * 时 WebView fetch 拦 (TypeError: Load failed, outbound_log.db 4085 records 铁证).
 * 走 Rust reqwest 中转 · CSP 保持严格 · 无外网白名单.
 *
 * 两个 helper:
 *   httpProxy(url, init)        — 一次性 request/response (JSON 类, 非流)
 *   httpProxyStream(url, init)  — SSE streaming (chat completions 用)
 *
 * 返 Web `Response`, 完全兼容 fetch() API (`.status` `.headers` `.body` `.json()` `.text()`).
 * chat.ts:592 `resp.body.getReader()` 无感继续工作.
 */

import { invoke } from "@tauri-apps/api/core";
import {
  DEFAULT_TRANSPORT_TIMEOUT_MS,
  LLM_TRANSPORT_TIMEOUT_MS,
} from "./timeouts";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

interface HttpProxyRequest {
  url: string;
  method: string;
  headers: Record<string, string>;
  body?: string;
  bodyBase64?: boolean;
  timeoutMs?: number;
}

interface HttpProxyResponse {
  status: number;
  headers: Record<string, string>;
  body: string;
  bodyBase64: boolean;
}

interface HttpProxyStreamStart {
  status: number;
  headers: Record<string, string>;
}

/** RequestInit → HttpProxyRequest (Rust command payload). */
function buildRequest(url: string, init?: RequestInit, timeoutMs?: number): HttpProxyRequest {
  const method = (init?.method || "GET").toUpperCase();

  const headers: Record<string, string> = {};
  if (init?.headers) {
    if (init.headers instanceof Headers) {
      init.headers.forEach((v, k) => {
        headers[k] = v;
      });
    } else if (Array.isArray(init.headers)) {
      init.headers.forEach(([k, v]) => {
        headers[k] = v;
      });
    } else {
      Object.assign(headers, init.headers);
    }
  }

  let body: string | undefined;
  let bodyBase64 = false;
  if (init?.body != null) {
    const b: unknown = init.body;
    if (typeof b === "string") {
      body = b;
    } else if (b instanceof Uint8Array) {
      // spread 大数组会 stack overflow (超 ~64k 参数) — loop 拼安全.
      let bin = "";
      for (let i = 0; i < b.length; i++) bin += String.fromCharCode(b[i]);
      body = btoa(bin);
      bodyBase64 = true;
    } else if (b instanceof ArrayBuffer) {
      const bytes = new Uint8Array(b);
      let bin = "";
      for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
      body = btoa(bin);
      bodyBase64 = true;
    } else {
      // Blob / FormData / URLSearchParams / ReadableStream 目前未支持. 用到再加.
      throw new Error(`httpProxy: body 类型未支持 (${Object.prototype.toString.call(b)})`);
    }
  }

  return { url, method, headers, body, bodyBase64, timeoutMs };
}

/** 这些状态码**不许带 body** —— 带了 `new Response()` 当场抛。
 *
 * 8/14 现场那条:
 *
 *     [advisory] fetch feed exception:
 *     TypeError: Response cannot have a body with the given status.
 *
 * advisory.ts 用 If-None-Match 做条件请求, 服务端正常返 304, 而这里
 * `new Response(resp.body, {status: 304})` —— body 是字符串 (哪怕是空串
 * `""` 也算 non-null), 于是构造器抛。
 *
 * 后果不是"少了一次刷新": ETag fast path **整条废掉** ——
 * 304 本来的含义是"你手上的缓存还新鲜, 用它", 现在变成了"拉取失败, 返 null",
 * 于是每次都当没有 feed。而日志里只有一行看着像网络问题的 TypeError。
 *
 * ⚠ 规范里的 null body status 是 101/103/204/205/304 五个, 这里**只列三个**。
 *
 * 1xx (101 Switching Protocols / 103 Early Hints) 根本构造不出 Response ——
 * `init.status` 必须在 200..599, 传 null 也一样抛 RangeError。也就是说对
 * 1xx 没有"正确的返回值"可给。
 *
 * 而它们也到不了这里: reqwest/hyper 把 1xx 当中间响应自己吃掉, 只把最终
 * 响应交出来; 101 只在我们主动发 upgrade 请求时才可能是终态, 而这条代理
 * 不做 upgrade。真要哪天出现, 抛出来比编一个假状态码好。
 */
const NULL_BODY_STATUS = new Set([204, 205, 304]);

function toResponseHeaders(headers: Record<string, string>): Headers {
  const h = new Headers();
  for (const [k, v] of Object.entries(headers)) {
    try {
      h.set(k, v);
    } catch {
      // 无效 header key (罕见 · e.g. non-ASCII) 跳过 · 不阻塞主流程
    }
  }
  return h;
}


// 超时的值和来龙去脉都在 timeouts.ts —— 8/10 这里曾经写死过一个 30 秒,
// 掐断了 advisor 的 agent loop, 查了一整天。别再在这个文件里放裸数字。

/** 这次请求是不是 LLM 调用 (agent loop 可能跑几分钟)。
 *
 * 判 URL 而不是让每个 caller 自己传 —— main.tsx 把 window.fetch 全局 patch 到
 * 这里, 全项目几十处 fetch 都经过, 靠 caller 记得传参数是不现实的 (今天这个
 * bug 就是这么来的: 30 秒写死在这一行, 没有任何 caller 知道自己被限了 30 秒)。
 */
function isLlmCall(url: string): boolean {
  return /\/(v1\/chat\/completions|v1\/responses|api\/chat)\b/.test(url);
}

/** 只给单测用 —— 判据是个正则, 写错一个字符就静默失效 (回到 30 秒必挂),
 *  而那种失效在界面上跟"上游慢"长得一模一样。必须能被测到。 */
export const __isLlmCallForTest = isLlmCall;

/** 非 stream · 一次性 fetch. 用于 JSON API / metadata 类调用. */
export async function httpProxy(url: string, init?: RequestInit): Promise<Response> {
  const req = buildRequest(
    url, init,
    isLlmCall(url) ? LLM_TRANSPORT_TIMEOUT_MS : DEFAULT_TRANSPORT_TIMEOUT_MS,
  );
  const resp = await invoke<HttpProxyResponse>("http_proxy", { req });

  // 名字不能叫 init —— 会盖住函数参数 init: RequestInit
  const respInit = { status: resp.status, headers: toResponseHeaders(resp.headers) };

  // 304 / 204 这类必须 body=null, 见 NULL_BODY_STATUS 上面那段。
  // 注意 **不能**只判空串: 服务端完全可以在 304 上回一段无意义的字节,
  // 判据是状态码, 不是 body 内容。
  if (NULL_BODY_STATUS.has(resp.status)) {
    return new Response(null, respInit);
  }

  // 分路让 TS 能 narrow bodyBytes 类型 (union 里混了 URLSearchParams 会报 TS2345).
  if (resp.bodyBase64) {
    const bin = atob(resp.body);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new Response(bytes, respInit);
  }
  return new Response(resp.body, respInit);
}

/** SSE 流式 fetch. 用于 /v1/chat/completions. 返 Response, body 是 ReadableStream. */
export async function httpProxyStream(url: string, init?: RequestInit): Promise<Response> {
  const requestId = crypto.randomUUID();
  const encoder = new TextEncoder();

  let unlistenChunk: UnlistenFn | undefined;
  let unlistenDone: UnlistenFn | undefined;
  let unlistenError: UnlistenFn | undefined;

  const cleanup = () => {
    unlistenChunk?.();
    unlistenDone?.();
    unlistenError?.();
  };

  // 先 setup listener, 后 invoke — 防漏 chunk.
  // ReadableStream start() 是同步的, 但 listen() 是 async. 用 promise 保证 3 个都注册再 invoke.
  const listenersReady = Promise.all([
    listen<string>(`http_proxy_chunk_${requestId}`, () => {}), // 占位, 真 handler 在 stream start
    listen(`http_proxy_done_${requestId}`, () => {}),
    listen<string>(`http_proxy_error_${requestId}`, () => {}),
  ]);
  // 立即 unregister 占位 — 真 handler 在 ReadableStream start 里 register.
  listenersReady.then((fns) => fns.forEach((f) => f()));

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      try {
        unlistenChunk = await listen<string>(`http_proxy_chunk_${requestId}`, (event) => {
          try {
            controller.enqueue(encoder.encode(event.payload));
          } catch {
            // controller 已 closed (被 done event 先关了) — 忽略
          }
        });
        unlistenDone = await listen(`http_proxy_done_${requestId}`, () => {
          try {
            controller.close();
          } catch {
            /* 已 closed */
          }
          cleanup();
        });
        unlistenError = await listen<string>(`http_proxy_error_${requestId}`, (event) => {
          try {
            controller.error(new Error(event.payload || "http_proxy stream 错"));
          } catch {
            /* 已 closed */
          }
          cleanup();
        });
      } catch (e) {
        controller.error(e instanceof Error ? e : new Error(String(e)));
        cleanup();
      }
    },
    cancel() {
      // 前端 abort (chat.ts internalCtrl.abort()) → 通知 Rust 停 stream
      invoke("http_proxy_abort", { requestId }).catch(() => {});
      cleanup();
    },
  });

  // Abort signal 桥 (fetch(init) 的 signal 会经 init 传下来, chat.ts internalCtrl.signal)
  if (init?.signal) {
    if (init.signal.aborted) {
      // 已 abort — 立即取消, 返空 stream
      invoke("http_proxy_abort", { requestId }).catch(() => {});
    } else {
      init.signal.addEventListener(
        "abort",
        () => {
          invoke("http_proxy_abort", { requestId }).catch(() => {});
        },
        { once: true },
      );
    }
  }

  // SSE 走同一个 LLM 传输超时 (见 timeouts.ts); chat.ts 里再套 idle timer
  const req = buildRequest(url, init, LLM_TRANSPORT_TIMEOUT_MS);
  let start: HttpProxyStreamStart;
  try {
    start = await invoke<HttpProxyStreamStart>("http_proxy_stream", { req, requestId });
  } catch (e) {
    cleanup();
    throw e;
  }

  // 流式这边同样要判 —— SSE 正常不会是 304, 但"正常不会"不是不判的理由:
  // 真出现时抛的是同一个 TypeError, 而那时错误信息指向的是 chat 主链路。
  const streamInit = { status: start.status, headers: toResponseHeaders(start.headers) };
  if (NULL_BODY_STATUS.has(start.status)) {
    void stream.cancel().catch(() => {});   // 没人读, 顺手关掉 Rust 那边的流
    return new Response(null, streamInit);
  }
  return new Response(stream, streamInit);
}

/** Auto-detect: URL 或 body 是否 stream 请求 (chat completions / SSE). */
export function isStreamRequest(url: string, init?: RequestInit): boolean {
  // 8/7: body 里明写 stream:false 的, **胜过下面的 URL 猜测**。
  //
  // 原来只按 URL 判 —— 只要打到 /v1/chat/completions 就一律当流式, 于是
  // 所有非流式调用 (拟稿 / briefing / profile / wikiLinkSuggest 共 10 处明确
  // 发 stream:false) 都被塞进 SSE 通道: 注册 event listener、等 done 事件、
  // 再把整段拼回来当普通 Response 返 —— 绕一大圈拿同一个结果。
  //
  // URL 猜测本身没错 (chat 绝大多数确实是流式), 错在它盖过了请求自己的声明。
  // 显式优先于推断。
  if (typeof init?.body === "string" && /"stream"\s*:\s*false/.test(init.body)) {
    return false;
  }
  // /v1/chat/completions + /v1/responses stream 都常见
  if (url.includes("/v1/chat/completions") || url.includes("/v1/responses")) {
    return true;
  }
  // OpenAI 风格 body 里的 stream: true
  if (typeof init?.body === "string" && /"stream"\s*:\s*true/.test(init.body)) {
    return true;
  }
  // gateway/hermes SSE endpoints
  if (url.includes("/api/sessions/") && url.includes("/chat/stream")) {
    return true;
  }
  return false;
}

/** 统一入口: fetch-like API, auto stream/非 stream. me.ts fetch* 内部替换 `fetch(url, init)` 用. */
export async function fetchViaProxy(url: string, init?: RequestInit): Promise<Response> {
  if (isStreamRequest(url, init)) {
    return httpProxyStream(url, init);
  }
  return httpProxy(url, init);
}
