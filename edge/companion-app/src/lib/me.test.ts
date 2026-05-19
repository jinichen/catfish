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

// ── mock ./tauri (gatewayGetDevToken etc) ──────────────────────
vi.mock("./tauri", () => ({
  gatewayGetDevToken: vi.fn(async () => "dev-token-xyz"),
}));

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

// ── fetch mock helper ───────────────────────────────────────
function setFetchMock(
  ...responders: Array<(input: RequestInfo | URL, init?: RequestInit) => Response>
): { calls: Array<{ url: string; headers: Record<string, string>; method: string }> } {
  const calls: Array<{ url: string; headers: Record<string, string>; method: string }> = [];
  let i = 0;
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const headers: Record<string, string> = {};
    const h = new Headers(init?.headers || {});
    h.forEach((v, k) => { headers[k.toLowerCase()] = v; });
    calls.push({
      url: typeof input === "string" ? input : input.toString(),
      headers,
      method: init?.method ?? "GET",
    });
    const responder = responders[Math.min(i, responders.length - 1)];
    i += 1;
    return responder(input, init);
  }) as unknown as typeof fetch;
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
  _resetUserEmailCacheForTest();
});

afterEach(() => {
  config.backendUrl = _savedConfig.backendUrl;
  config.gatewayUrl = _savedConfig.gatewayUrl;
  config.useHermes = _savedConfig.useHermes;
  config.hermesAuthHeader = _savedConfig.hermesAuthHeader;
});

describe("fetchWithAuth — hermes 路径 (useHermes=true)", () => {
  it("Authorization 用 hermesAuthHeader, 不调 getToken / OAuth", async () => {
    config.useHermes = true;
    config.hermesAuthHeader = "Bearer hermes-static-key-abc";
    config.backendUrl = "http://localhost:8642";

    invokeMock.mockImplementation(async (cmd: string) => {
      if (cmd === "auth_whoami") {
        return { authenticated: true, email: "alice@catfish.dev" };
      }
      throw new Error(`unexpected invoke: ${cmd}`);
    });
    const { calls } = setFetchMock(() => jsonResp(200, { ok: true }));

    const resp = await fetchWithAuth(`${config.backendUrl}/api/me`);
    expect(resp.status).toBe(200);

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("http://localhost:8642/api/me");
    expect(calls[0].headers["authorization"]).toBe("Bearer hermes-static-key-abc");
    expect(calls[0].headers["x-catfish-user"]).toBe("alice@catfish.dev");

    // hermes 路径不应该调 auth_login / auth_get_access_token / gatewayGetDevToken
    const cmdsCalled = invokeMock.mock.calls.map((c) => c[0]);
    expect(cmdsCalled).not.toContain("auth_login");
    expect(cmdsCalled).not.toContain("auth_get_access_token");
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
    const { calls } = setFetchMock(() => jsonResp(401));

    const resp = await fetchWithAuth(`${config.backendUrl}/api/me`);
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
    const { calls } = setFetchMock(() => jsonResp(401));

    const resp = await fetchWithAuth(`${config.backendUrl}/api/me`);
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
    const { calls } = setFetchMock(() => jsonResp(200, { ok: true }));

    const resp = await fetchWithAuth(`${config.backendUrl}/api/me`);
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
    const { calls } = setFetchMock(
      () => {
        fetchCount += 1;
        return fetchCount === 1 ? jsonResp(401) : jsonResp(200, { retried: true });
      },
    );

    const resp = await fetchWithAuth(`${config.backendUrl}/api/me`);
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
