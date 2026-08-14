/** 全局 fetch 代理不能劫持 Tauri 自己的 IPC (8/14 Windows 白屏)。
 *
 * # 病历
 *
 *     Failed to execute 'postMessage' on 'EmbeddedBrowserWebView': Invalid string length
 *     RangeError: Invalid string length
 *         at Object.postMessage (<anonymous>:1:102)
 *         at sendIpcMessage (<anonymous>:130:18)
 *         at <anonymous>:113:13
 *
 * macOS 一切正常, Windows 白屏。差别是 Tauri IPC 的 URL 形态分平台
 * (tauri 2.11.2 scripts/core.js:16-18):
 *
 *     Windows:  http://ipc.localhost/<cmd>     ← http:// 开头
 *     macOS:    ipc://localhost/<cmd>
 *
 * 而 main.tsx 的全局 fetch patch 判据是 `url.startsWith("http://")`。
 * 于是 Windows 上每次 invoke 都被劫进 Rust 代理, 代理内部又 invoke……
 *
 * # 这个文件钉什么
 *
 * 不是"这次判据改对了" —— 一张 URL 对照表随便怎么写都能绿。钉的是**行为**:
 * 拿 tauri 2.11.2 的 ipc-protocol.js 照抄一份 sendIpcMessage, 用真的
 * installFetchProxy 把代理装上去, 然后看会不会递归。
 *
 * 并且用当年那一行原样的老判据跑同一个 harness, 确认它**真的会炸** ——
 * 否则这个 harness 就是个摆设, 绿了也不说明任何事。
 */
import { describe, expect, it } from "vitest";

import { installFetchProxy, shouldProxyUrl } from "./fetchProxyPolicy";

// ── 判据本身 ──────────────────────────────────────────────

describe("shouldProxyUrl", () => {
  it("Tauri 内部协议一律不代理 (两个平台的两种形态)", () => {
    // Windows 形态 —— 8/14 炸掉的就是这几个
    expect(shouldProxyUrl("http://ipc.localhost/bootstrap_endpoints")).toBe(false);
    expect(shouldProxyUrl("http://tauri.localhost/index.html")).toBe(false);
    expect(shouldProxyUrl("http://asset.localhost/C%3A%5Ctmp%5Ca.wav")).toBe(false);
    // 万一哪天开了 https scheme
    expect(shouldProxyUrl("https://ipc.localhost/x")).toBe(false);
    // macOS / Linux 形态
    expect(shouldProxyUrl("ipc://localhost/bootstrap_endpoints")).toBe(false);
    expect(shouldProxyUrl("tauri://localhost/index.html")).toBe(false);
    expect(shouldProxyUrl("asset://localhost/tmp/a.wav")).toBe(false);
  });

  it("★ 裸 localhost / 127.0.0.1 仍然要走代理", () => {
    // 这是 hermes / gateway 的真实地址。Windows 上页面源是
    // http://tauri.localhost, 直连它们是跨源, 会触发 CORS 预检 ——
    // 代理存在的意义之一就是绕开这个。
    //
    // 判据必须用 hostname 后缀而不是"含 localhost", 否则这两条会被
    // 一起放行, 换来另一个只在 Windows 上出现的故障。
    expect(shouldProxyUrl("http://localhost:8788/api/health")).toBe(true);
    expect(shouldProxyUrl("http://127.0.0.1:8000/v1/models")).toBe(true);
  });

  it("远端网关走代理 (代理本来的用途)", () => {
    expect(shouldProxyUrl("http://192.168.31.20:8000/v1/chat/completions")).toBe(true);
    expect(shouldProxyUrl("https://gateway.example.com/v1/models")).toBe(true);
  });

  it("非 http(s) 一律原生", () => {
    for (const u of ["blob:abc", "data:text/plain,x", "file:///a", "/api/x", "./x.json"]) {
      expect(shouldProxyUrl(u)).toBe(false);
    }
  });
});

// ── 行为: 照抄 Tauri 的 IPC, 看会不会自己咬自己 ──────────────

/** tauri 2.11.2 scripts/core.js:14-19 —— convertFileSrc 的平台分支。 */
function convertFileSrc(path: string, protocol: string, osName: "windows" | "macos"): string {
  const p = encodeURIComponent(path);
  return osName === "windows"
    ? `http://${protocol}.localhost/${p}`
    : `${protocol}://localhost/${p}`;
}

/** 递归深度封顶。
 *
 * 真机上没有这个封顶 —— 它是一路递归到 V8 字符串上限才抛 RangeError 的。
 * 这里第一版就没设封顶跑过一次: **9 秒后抛出的正是生产那句
 * `Invalid string length`**, 深度约 23 层 (每层约翻倍, 60B × 2^23 ≈ 512MB)。
 *
 * 也就是说这个 harness 复现的不是"某种递归", 就是那个 bug 本身。
 *
 * 但一个单测跑 9 秒、峰值吃掉 1GB 内存不合适, 所以封到 12 层。12 层只要
 * 几百 KB, 瞬间返回, 而"指数增长 → 必然撞上限"由下面的算术断言接着钉。
 */
const MAX_DEPTH = 12;

interface Harness {
  /** Tauri 的 invoke —— 会经过 sendIpcMessage → fetch */
  invoke: (cmd: string, payload: unknown) => Promise<void>;
  /** 装了代理之后的 fetch (相当于生产里的 window.fetch) */
  scope: { fetch: typeof fetch };
  /** 每层 IPC 请求体的长度, 用来看有没有指数膨胀 */
  bodyLengths: number[];
  /** 每层 IPC 的 cmd, 按发生顺序 */
  cmds: string[];
  restore: () => void;
}

/**
 * 搭一个最小但忠实的 Tauri IPC + 全局 fetch 代理。
 *
 * @param osName            平台 (决定 IPC 的 URL 形态)
 * @param installProxy      怎么装代理 (真的 installFetchProxy / 老判据)
 */
function makeHarness(
  osName: "windows" | "macos",
  installProxy: (
    proxyFetch: (url: string, init?: RequestInit) => Promise<Response>,
    scope: { fetch: typeof fetch },
  ) => () => void,
): Harness {
  const bodyLengths: number[] = [];
  const cmds: string[] = [];
  let depth = 0;

  // 原生 fetch: 真到达 Rust 的那一端。只记账, 回一个空 JSON。
  const scope = {
    fetch: (async (_input: RequestInfo | URL, _init?: RequestInit) => {
      return {
        headers: {
          get: (k: string) =>
            k.toLowerCase() === "content-type" ? "application/json" : "ok",
        },
        json: async () => null,
      } as unknown as Response;
    }) as typeof fetch,
  };

  // tauri 2.11.2 scripts/ipc-protocol.js:22-86 的自定义协议分支
  function sendIpcMessage(cmd: string, payload: unknown): Promise<void> {
    if (++depth > MAX_DEPTH) {
      // 真机上这里是一路递归到 V8 字符串上限才抛 RangeError。
      // 测试里不能真等它涨到 512MB, 用深度封顶代替。
      throw new Error(`IPC 递归深度超过 ${MAX_DEPTH} —— 代理把 IPC 自己劫走了`);
    }
    const body = JSON.stringify({ cmd, payload });
    bodyLengths.push(body.length);
    cmds.push(cmd);
    return scope
      .fetch(convertFileSrc(cmd, "ipc", osName), { method: "POST", body })
      .then(() => undefined);
  }

  // http_proxy.ts 的 fetchViaProxy: 它自己就是一次 invoke
  const proxyFetch = async (url: string, init?: RequestInit): Promise<Response> => {
    await sendIpcMessage("http_proxy", {
      req: { url, method: init?.method ?? "GET", body: init?.body ?? null },
    });
    return { ok: true } as Response;
  };

  const restore = installProxy(proxyFetch, scope);
  return {
    invoke: (cmd, payload) => sendIpcMessage(cmd, payload),
    scope,
    bodyLengths,
    cmds,
    restore,
  };
}

describe("Tauri IPC 不能被自己的 fetch 代理劫走", () => {
  it("★★★ Windows: invoke 一次就是一次, 不递归", async () => {
    const h = makeHarness("windows", installFetchProxy);
    try {
      await h.invoke("bootstrap_endpoints", { a: 1 });
      expect(h.bodyLengths.length).toBe(1);
    } finally {
      h.restore();
    }
  });

  it("macOS 同样只走一层 (原来就是对的, 别改坏)", async () => {
    const h = makeHarness("macos", installFetchProxy);
    try {
      await h.invoke("bootstrap_endpoints", { a: 1 });
      expect(h.bodyLengths.length).toBe(1);
    } finally {
      h.restore();
    }
  });

  it("★★ 真的远端请求还是要经过代理 —— 别修过头把代理整个关了", async () => {
    const h = makeHarness("windows", installFetchProxy);
    try {
      await h.scope.fetch("http://10.0.0.9:8000/v1/models");
      // 走代理 = 触发了一次 http_proxy 的 IPC
      expect(h.cmds).toEqual(["http_proxy"]);
    } finally {
      h.restore();
    }
  });
});

describe("变异测试: 换回 8/14 那行老判据, harness 必须变红", () => {
  /** main.tsx 里被删掉的那一行, 一字不改。 */
  function installNaiveProxy(
    proxyFetch: (url: string, init?: RequestInit) => Promise<Response>,
    scope: { fetch: typeof fetch },
  ): () => void {
    const originalFetch = scope.fetch;
    scope.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("http://") || url.startsWith("https://")) {
        return proxyFetch(url, init);
      }
      return originalFetch(input, init);
    }) as typeof fetch;
    return () => {
      scope.fetch = originalFetch;
    };
  }

  it("★★★ Windows + 老判据 = 无限递归 (这就是白屏)", async () => {
    const h = makeHarness("windows", installNaiveProxy);
    try {
      await expect(h.invoke("bootstrap_endpoints", { a: 1 })).rejects.toThrow(/递归深度/);

      // 光"递归了"还不够 —— 得是**指数**增长, 那才是 Invalid string length
      // 的来源 (线性增长的话再多层也撞不到 512MB)。
      // 每层把上一层的请求体整个 JSON 转义塞进 req.body, 引号全变 \", 大约翻倍。
      //
      // 实测: 头几层被固定的包装开销摊薄, 最低 1.61; 越往后越接近 2 (最后一层 1.98)。
      // 所以下界取 1.5, 另外单独钉住"尾部确实逼近 2" —— 只写一个 min 的话,
      // 一个"每层加常数"的假增长也能混过去。
      const factors = h.bodyLengths
        .slice(1)
        .map((n, i) => n / h.bodyLengths[i]);
      expect(Math.min(...factors)).toBeGreaterThan(1.5);
      expect(factors.at(-1)!).toBeGreaterThan(1.9);

      // 照这个倍率推下去, 多少层会撞上 V8 的字符串上限 (2^29-24 ≈ 5.4 亿字符)?
      // 算出来是 20 来层 —— 跟真机一致, 而真机没有封顶, 所以它一定会撞上。
      const V8_MAX_STRING = 2 ** 29 - 24;
      const growth = Math.min(...factors);
      const levelsToBlowUp =
        Math.log(V8_MAX_STRING / h.bodyLengths[0]) / Math.log(growth);
      expect(levelsToBlowUp).toBeLessThan(40);
    } finally {
      h.restore();
    }
  });

  it("macOS + 老判据 = 正常 —— 这正是它躲过所有 mac 测试的原因", async () => {
    const h = makeHarness("macos", installNaiveProxy);
    try {
      await h.invoke("bootstrap_endpoints", { a: 1 });
      expect(h.bodyLengths.length).toBe(1);
    } finally {
      h.restore();
    }
  });
});

// ── 防回流 ────────────────────────────────────────────────

describe("main.tsx 不许把判据重新内联回去", () => {
  /** 这条是给下一个人看的。
   *
   * 上面所有测试都只测 fetchProxyPolicy.ts。如果哪天有人图省事又在 main.tsx
   * 里直接改 window.fetch, 那些测试照样全绿, 而线上又白屏一次 —— 8/14 之前
   * 的状态正是"判据没有任何测试", 不是"判据写错了"。
   */
  it("★★ 只能通过 installFetchProxy 装, 不能直接碰 window.fetch", async () => {
    const { readFileSync } = await import("node:fs");
    const { fileURLToPath } = await import("node:url");
    const path = await import("node:path");
    const here = path.dirname(fileURLToPath(import.meta.url));
    const mainPath = path.resolve(here, "..", "main.tsx");

    const src = readFileSync(mainPath, "utf-8");
    // 判据文件确实被用上了 (路径写错 / 改名的话这条会红, 不会静默跳过)
    expect(src).toContain("installFetchProxy");
    expect(src).toMatch(/from\s+"\.\/lib\/fetchProxyPolicy"/);

    const code = src
      .split("\n")
      .filter((l) => !l.trim().startsWith("//") && !l.trim().startsWith("*"))
      .join("\n");
    expect(code).not.toMatch(/window\.fetch\s*=/);
    expect(code).not.toMatch(/startsWith\(\s*["']https?:\/\//);
  });
});
