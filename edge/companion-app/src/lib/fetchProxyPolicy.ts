/** 全局 fetch 代理的**唯一**判据: 这个 URL 该走 Rust 代理, 还是走原生 fetch。
 *
 * # 为什么要单独一个文件
 *
 * 这个判据原来是 main.tsx 里 `installFetchProxy` 中的一行:
 *
 *     if (url.startsWith("http://") || url.startsWith("https://"))
 *
 * 一行, 藏在 IIFE 里, 不可 import, 因此**没有任何测试覆盖**。而它管着全项目
 * 每一次 fetch —— 包括 Tauri 自己的 IPC。8/14 Windows 白屏就是它。
 *
 * # 病历: Windows 白屏 (8/14)
 *
 *     Failed to execute 'postMessage' on 'EmbeddedBrowserWebView': Invalid string length
 *     RangeError: Invalid string length
 *         at Object.postMessage (<anonymous>:1:102)
 *         at sendIpcMessage (<anonymous>:130:18)
 *         at <anonymous>:113:13
 *
 * 那两个 <anonymous> 是 Tauri 注入的 `ipc-protocol.js`。把 tauri 2.11.2 的
 * scripts/ipc-protocol.js 跟 scripts/process-ipc-message-fn.js (46 行, 内联
 * 进去顶掉第 14 行) 拼起来对行号, 偏移正好 +46, 两帧的行**和列**都严丝合缝:
 *
 *     注入 130:18 → 源码 84:18  `window.ipc.postMessage(data)`   ← postMessage 兜底分支
 *     注入 113:13 → 源码 67:13  `sendIpcMessage(message)`        ← 自定义协议失败后的重试
 *
 * 也就是说: **Tauri 的自定义协议 IPC 先失败了**, 才退到 postMessage, 然后
 * postMessage 收到一个长到爆 V8 字符串上限的串。
 *
 * ## 为什么自定义协议会失败, 而且只在 Windows 上
 *
 * Tauri v2 的 IPC 走 `fetch(convertFileSrc(cmd, 'ipc'), {method:'POST', ...})`
 * (ipc-protocol.js:37)。而 convertFileSrc 的 URL 形态**分平台**
 * (tauri 2.11.2 scripts/core.js:16-18):
 *
 *     Windows / Android:  http://ipc.localhost/<cmd>      ← http:// 开头!
 *     macOS / Linux:      ipc://localhost/<cmd>
 *
 * 老判据只看 `startsWith("http://")`, 于是:
 *
 *     macOS   ipc://localhost/xxx      → 不是 http:// → 原生 fetch → IPC 正常
 *     Windows http://ipc.localhost/xxx → 是 http://   → 被劫进 fetchViaProxy
 *
 * 而 fetchViaProxy 内部是 `invoke("http_proxy", ...)` —— invoke 又走 IPC,
 * IPC 又 fetch `http://ipc.localhost/http_proxy`, 又被劫……**无限递归**。
 *
 * 每递归一层, 上一层的整个请求体被 JSON.stringify 塞进这一层的 `req.body`
 * 字符串里, 引号全部转义, 长度大约翻倍。几十层之后就撞上 V8 的字符串上限,
 * 抛 RangeError → 那一层的 fetch reject → Tauri 认为"自定义协议不可用"
 * (ipc-protocol.js:60-67) → 退到 postMessage 重发同一条巨串 → 就是上面那行报错。
 *
 * 界面白屏是因为这一切发生在 main.tsx 的 bootstrapEndpoints() 里, render 之前。
 *
 * # 现在的判据
 *
 * Windows 上 Tauri 把自己所有的内部协议都映射成 `<协议>.localhost`
 * (ipc / tauri / asset, 插件还会加更多)。所以放行整个 `*.localhost` 子域,
 * 而不是逐个列举 —— 列举的话下次 Tauri 或插件加一个新协议又会踩。
 *
 * ⚠ 注意**不能**顺手放行裸 `localhost` / `127.0.0.1`: 那是 hermes / gateway
 * 的真实地址, 走原生 fetch 会变成跨源请求 (Windows 上页面源是
 * http://tauri.localhost), 触发 CORS 预检。代理存在的意义之一就是绕开这个。
 * 判据用 hostname 后缀而不是字符串包含, 正好把 `localhost:8788` 排除在外。
 */

/** Tauri 在 Windows 上给内部协议用的主机名后缀 (`ipc.localhost` / `asset.localhost` / …)。 */
const TAURI_INTERNAL_HOST_SUFFIX = ".localhost";

/**
 * 这个 URL 要不要走 Rust reqwest 代理?
 *
 * @returns true = 走 fetchViaProxy; false = 走原生 fetch
 */
export function shouldProxyUrl(url: string): boolean {
  // 非 http(s) 一律原生: tauri: / ipc: / asset: / blob: / data: / file: / 相对路径。
  // CSP 里 'self' 就覆盖了这些, 代理反而会坏事。
  if (!/^https?:\/\//i.test(url)) return false;

  let hostname: string;
  try {
    hostname = new URL(url).hostname.toLowerCase();
  } catch {
    // 连 URL 都解析不了的, 交给原生 fetch 去报它自己的错 —— 别让代理把
    // "URL 非法"翻译成一个看不懂的 Rust 错误。
    return false;
  }

  // Tauri 内部协议 (Windows 形态)。放行, 否则就是上面那个无限递归。
  if (hostname.endsWith(TAURI_INTERNAL_HOST_SUFFIX)) return false;

  return true;
}

/** 装全局 fetch 代理。
 *
 * 放在这里而不是 main.tsx 里的 IIFE, 是为了让测试能拿真的东西跑 ——
 * 原来那版判据从来没被测过, 正因为它锁在一个不可 import 的 IIFE 里。
 *
 * @param scope 默认 globalThis; 测试传假 window
 * @param proxyFetch 命中判据时用它 (生产是 fetchViaProxy)
 * @returns 还原函数, 测试用完恢复现场
 */
export function installFetchProxy(
  proxyFetch: (url: string, init?: RequestInit) => Promise<Response>,
  scope: { fetch: typeof fetch } = globalThis as unknown as { fetch: typeof fetch },
): () => void {
  const originalFetch = scope.fetch.bind(scope);

  scope.fetch = async (
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> => {
    let url: string;
    if (typeof input === "string") {
      url = input;
    } else if (input instanceof URL) {
      url = input.href;
    } else if (input && typeof (input as Request).url === "string") {
      url = (input as Request).url;
    } else {
      // 不认识的 input 类型, 保守走原生
      return originalFetch(input as RequestInfo, init);
    }

    if (shouldProxyUrl(url)) return proxyFetch(url, init);
    return originalFetch(input as RequestInfo, init);
  };

  return () => {
    scope.fetch = originalFetch;
  };
}
