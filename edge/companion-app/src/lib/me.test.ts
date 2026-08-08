/** BL-AUTH-DECOUPLE-A5 Phase 2 (5/19) — fetchWithAuth 行为单测.
 *
 * 验证 wrapper 按 config.useHermes / config.hermesAuthHeader 切换两条路径:
 *   1. hermes 路径 (useHermes=true + hermesAuthHeader set):
 *      - Authorization = hermesAuthHeader (静态 API_SERVER_KEY, 不是 OAuth JWT)
 *      - X-Catfish-User = 当前员工 email (从 auth_whoami 拿)
 *      - 不 retry 401 (静态 key 没 reauth 意义)
 *   2. 灰度回退路径 (useHermes=false):
 *      - Authorization = OAuth Bearer (走 getToken: keychain → dev_token → fallback)
 *      - 401 → invoke('auth_login') → 重发一次
 *
 * 跑法: cd edge/companion-app && pnpm exec vitest src/lib/me.test.ts
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── mock @tauri-apps/api/core (Tauri command invoke) ──────────
const invokeMock = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}));

// http_proxy_stream 注册 Tauri event listeners；在单测中只需提供可注销的 listener。
// 响应本身由下方 setProxyMock 通过 invoke("http_proxy[_stream]") 返回。
const listenMock = vi.fn(async () => vi.fn());
vi.mock("@tauri-apps/api/event", () => ({
  listen: (...args: unknown[]) => listenMock(...args),
}));

// ── mock ./tauri ──────────────────────
// P39 (5/22 gateway 解耦收尾): gatewayGetDevToken 已删, mock 保空对象兼容
// (me.ts 现只 import hermesApiConfigGet / hermesApiAuthHeader / fetchProactiveContext,
//  但 test 里 fetchWithAuth 走的 OAuth path 不用它们, 空 mock 即可).
vi.mock("./tauri", () => ({}));

// ── 假 localStorage ──
const _store = new Map<string, string>();
(globalThis as unknown as { localStorage: Storage }).localStorage = {
  length: 0,
  getItem: (k: string) => _store.get(k) ?? null,
  setItem: (k: string, v: string) => { _store.set(k, v); },
  removeItem: (k: string) => { _store.delete(k); },
  clear: () => _store.clear(),
  key: () => null,
};

// ── 假 window.dispatchEvent (auth-refreshed 通知) ──
(globalThis as unknown as { window: Window }).window =
  (globalThis as unknown as { window: Window }).window ??
  ({
    dispatchEvent: () => true,
  } as unknown as Window);

// import 必须在 mock 之后
import { config } from "./env";
import { fetchWithAuth, _resetUserEmailCacheForTest } from "./me";

// ── Rust HTTP proxy mock helper ─────────────────────────────
// fetchWithAuth 现在经 Tauri 的 http_proxy / http_proxy_stream 发请求。保留原测试
// 对 URL、headers 和 401 retry 的断言，但从 proxy command payload 取请求资料。
function setProxyMock(
  ...responders: Array<() => Response>
): { calls: Array<{ url: string; headers: Record<string, string>; method: string }> } {
  const calls: Array<{ url: string; headers: Record<string, string>; method: string }> = [];
  let i = 0;
  const authInvoke = invokeMock.getMockImplementation();
  invokeMock.mockImplementation(async (cmd: string, payload?: {
    req?: { url: string; method: string; headers: Record<string, string> };
  }) => {
    if (cmd !== "http_proxy" && cmd !== "http_proxy_stream") {
      return authInvoke?.(cmd, payload);
    }

    const req = payload?.req;
    if (!req) throw new Error(`missing proxy request for ${cmd}`);
    const headers: Record<string, string> = {};
    for (const [key, value] of Object.entries(req.headers)) {
      headers[key.toLowerCase()] = value;
    }
    calls.push({ url: req.url, headers, method: req.method });
    const responder = responders[Math.min(i, responders.length - 1)];
    i += 1;
    const response = responder();
    const responseHeaders = Object.fromEntries(response.headers.entries());

    if (cmd === "http_proxy_stream") {
      return { status: response.status, headers: responseHeaders };
    }
    return {
      status: response.status,
      headers: responseHeaders,
      body: await response.text(),
      bodyBase64: false,
    };
  });
  return { calls };
}

function jsonResp(status: number, body: unknown = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// ── snapshot / restore config 字段 ──
const _savedConfig = {
  backendUrl: config.backendUrl,
  gatewayUrl: config.gatewayUrl,
  useHermes: config.useHermes,
  hermesAuthHeader: config.hermesAuthHeader,
};

beforeEach(() => {
  invokeMock.mockReset();
  listenMock.mockReset();
  listenMock.mockResolvedValue(vi.fn());
  _resetUserEmailCacheForTest();
});

afterEach(() => {
  config.backendUrl = _savedConfig.backendUrl;
  config.gatewayUrl = _savedConfig.gatewayUrl;
  config.useHermes = _savedConfig.useHermes;
  config.hermesAuthHeader = _savedConfig.hermesAuthHeader;
});

describe("fetchWithAuth — hermes 路径 (useHermes=true)", () => {
  // C2 (6/6 鸿波 CI matrix audit): test URL 改 /v1/chat/completions —
  // me.ts:165 BL-API-PATH-AWARE-AUTH (6/2 加) 把 /api/* + /v1/catalog 强制走
  // OAuth 直连 gateway (避开 hermes P7 catch-all token swap bug). 这 3 个 test
  // 5/19 写时假设 /api/me 走 hermes path, 6/2 改实现后 test 没同步 → CI 挂.
  // 改测真走 hermes path 的 URL (/v1/chat/completions, 不含 /api/ 不含 /v1/catalog).
  it("/v1/chat/completions Authorization 用 hermesAuthHeader, 不调 OAuth", async () => {
    config.useHermes = true;
    config.hermesAuthHeader = "Bearer hermes-static-key-abc";
    config.backendUrl = "http://localhost:8642";

    invokeMock.mockImplementation(async (cmd: string) => {
      if (cmd === "auth_whoami") {
        return { authenticated: true, email: "alice@catfish.dev" };
      }
      throw new Error(`unexpected invoke: ${cmd}`);
    });
    const { calls } = setProxyMock(() => jsonResp(200, { ok: true }));

    const resp = await fetchWithAuth(`${config.backendUrl}/v1/chat/completions`);
    expect(resp.status).toBe(200);

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("http://localhost:8642/v1/chat/completions");
    expect(calls[0].headers["authorization"]).toBe("Bearer hermes-static-key-abc");
    expect(calls[0].headers["x-catfish-user"]).toBe("alice@catfish.dev");

    const cmdsCalled = invokeMock.mock.calls.map((c) => c[0]);
    expect(cmdsCalled).not.toContain("auth_login");
    expect(cmdsCalled).not.toContain("auth_get_access_token");
  });

  // 8/9 P44 回归: /api/catfish/* 是 hermes 自己的端点 (注册在 8642, 走
  // adapter._check_auth 要 API_SERVER_KEY)。默认规则"含 /api/ 就是 gateway"
  // 会让它拿 OAuth JWT → 401 → auth_login → **反复弹登录页**。
  // 这条钉住它走 hermes path。
  it("/api/catfish/* 走 hermes 静态 key，不拿 OAuth JWT（否则会反复弹登录页）", async () => {
    config.useHermes = true;
    config.hermesAuthHeader = "Bearer hermes-static-key-abc";
    config.backendUrl = "http://localhost:8642";

    invokeMock.mockImplementation(async (cmd: string) => {
      if (cmd === "auth_whoami") {
        return { authenticated: true, email: "alice@catfish.dev" };
      }
      throw new Error(`unexpected invoke: ${cmd}`);
    });
    const { calls } = setProxyMock(() => jsonResp(200, { available: false, turns: [] }));

    const resp = await fetchWithAuth(`${config.backendUrl}/api/catfish/agent-activity`);
    expect(resp.status).toBe(200);
    expect(calls[0].headers["authorization"]).toBe("Bearer hermes-static-key-abc");

    const cmdsCalled = invokeMock.mock.calls.map((c) => c[0]);
    expect(cmdsCalled).not.toContain("auth_login");
    expect(cmdsCalled).not.toContain("auth_get_access_token");
  });

  it("普通 /api/* 仍走 gateway OAuth —— 别把上面那条修过头", async () => {
    config.useHermes = true;
    config.hermesAuthHeader = "Bearer hermes-static-key-abc";
    config.backendUrl = "http://localhost:8642";

    invokeMock.mockImplementation(async (cmd: string) => {
      if (cmd === "auth_get_access_token") return "oauth-jwt-token";
      if (cmd === "auth_whoami") {
        return { authenticated: true, email: "alice@catfish.dev" };
      }
      throw new Error(`unexpected invoke: ${cmd}`);
    });
    const { calls } = setProxyMock(() => jsonResp(200, { ok: true }));

    await fetchWithAuth(`${config.backendUrl}/api/quota/me`);
    expect(calls[0].headers["authorization"]).toBe("Bearer oauth-jwt-token");
  });

  it("auth_whoami 失败 (没登录) → 不带 X-Catfish-User, Authorization 仍带", async () => {
    config.useHermes = true;
    config.hermesAuthHeader = "Bearer hermes-key-xyz";
    config.backendUrl = "http://localhost:8642";

    invokeMock.mockImplementation(async (cmd: string) => {
      if (cmd === "auth_whoami") {
        throw new Error("no keychain token");
      }
      throw new Error(`unexpected invoke: ${cmd}`);
    });
    const { calls } = setProxyMock(() => jsonResp(401));

    const resp = await fetchWithAuth(`${config.backendUrl}/v1/chat/completions`);
    // hermes 路径**不 retry** 401, 直接透传
    expect(resp.status).toBe(401);
    expect(calls).toHaveLength(1);
    expect(calls[0].headers["authorization"]).toBe("Bearer hermes-key-xyz");
    expect(calls[0].headers["x-catfish-user"]).toBeUndefined();
  });

  it("401 不触发 reauth — 静态 key 没 reauth 意义", async () => {
    config.useHermes = true;
    config.hermesAuthHeader = "Bearer hermes-static-key";
    config.backendUrl = "http://localhost:8642";

    invokeMock.mockImplementation(async (cmd: string) => {
      if (cmd === "auth_whoami") {
        return { authenticated: true, email: "bob@x.com" };
      }
      throw new Error(`unexpected invoke: ${cmd}`);
    });
    const { calls } = setProxyMock(() => jsonResp(401));

    const resp = await fetchWithAuth(`${config.backendUrl}/v1/chat/completions`);
    expect(resp.status).toBe(401);
    expect(calls).toHaveLength(1);  // 只发一次, 不 retry
    expect(invokeMock.mock.calls.find((c) => c[0] === "auth_login")).toBeUndefined();
  });
});

describe("fetchWithAuth — 灰度回退路径 (useHermes=false)", () => {
  it("Authorization 用 OAuth Bearer (getToken 拿到的), URL = backendUrl", async () => {
    config.useHermes = false;
    config.hermesAuthHeader = null;
    config.backendUrl = "http://127.0.0.1:8999";

    invokeMock.mockImplementation(async (cmd: string) => {
      if (cmd === "auth_get_access_token") return "oauth-jwt-12345";
      throw new Error(`unexpected invoke: ${cmd}`);
    });
    const { calls } = setProxyMock(() => jsonResp(200, { ok: true }));

    const resp = await fetchWithAuth(`${config.gatewayUrl}/api/me`);
    expect(resp.status).toBe(200);
    expect(calls).toHaveLength(1);
    expect(calls[0].headers["authorization"]).toBe("Bearer oauth-jwt-12345");
    // 老路径**不**应该带 X-Catfish-User (老路径 gateway 自己从 JWT 解 user)
    expect(calls[0].headers["x-catfish-user"]).toBeUndefined();
  });

  it("401 → auth_login → 拿新 token → 重发一次", async () => {
    config.useHermes = false;
    config.hermesAuthHeader = null;
    config.backendUrl = "http://127.0.0.1:8999";

    let tokenSeq = 0;
    invokeMock.mockImplementation(async (cmd: string) => {
      if (cmd === "auth_get_access_token") {
        tokenSeq += 1;
        return tokenSeq === 1 ? "old-token-expired" : "new-token-fresh";
      }
      if (cmd === "auth_login") return null;
      throw new Error(`unexpected invoke: ${cmd}`);
    });

    let fetchCount = 0;
    const { calls } = setProxyMock(
      () => {
        fetchCount += 1;
        return fetchCount === 1 ? jsonResp(401) : jsonResp(200, { retried: true });
      },
    );

    const resp = await fetchWithAuth(`${config.gatewayUrl}/api/me`);
    expect(resp.status).toBe(200);
    expect(calls).toHaveLength(2);
    expect(calls[0].headers["authorization"]).toBe("Bearer old-token-expired");
    expect(calls[1].headers["authorization"]).toBe("Bearer new-token-fresh");
    expect(invokeMock.mock.calls.find((c) => c[0] === "auth_login")).toBeDefined();
  });
});

describe("config.backendUrl 切换 — gateway vs hermes URL", () => {
  it("用户 disable hermes_api → backendUrl 应该 = gatewayUrl (灰度回退)", () => {
    // 模拟 bootstrapEndpoints 决定不走 hermes 时的 state
    config.useHermes = false;
    config.hermesAuthHeader = null;
    config.backendUrl = config.gatewayUrl;
    expect(config.backendUrl).toBe(config.gatewayUrl);
    expect(config.useHermes).toBe(false);
  });

  it("用户 enable hermes_api → backendUrl 应该 = hermes URL", () => {
    config.useHermes = true;
    config.backendUrl = "http://localhost:8642";
    config.hermesAuthHeader = "Bearer some-key";
    expect(config.backendUrl).toBe("http://localhost:8642");
    expect(config.backendUrl).not.toBe(config.gatewayUrl);
    expect(config.useHermes).toBe(true);
  });
});
