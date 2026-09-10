/**
 * roomLinkHandshake.ts —— 握手载荷的编解码 + dispatch 组装 + 请求形态。
 *
 * 判据来自 hermes 源码, 不是本模块自己:
 *   - `_IDENTIFIER_RE` / `_DIGEST_RE` / from_mapping 的 17 个字段 (hosted_room_peer.py)
 *   - verify_room_grant 要求 grant 与 dispatch 的 8 个字段相等
 *   - Idempotency-Key 与 Authorization 形态 (api_server_room_dispatch.py / _room_grant_token)
 */
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./env", () => ({ config: { gatewayUrl: "http://central:8999" } }));

const fetchWithAuth = vi.fn();
vi.mock("./me", () => ({ fetchWithAuth: (...a: unknown[]) => fetchWithAuth(...a) }));

const fetchViaProxy = vi.fn();
vi.mock("./http_proxy", () => ({ fetchViaProxy: (...a: unknown[]) => fetchViaProxy(...a) }));

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a) }));

import {
  buildDispatch,
  decodeGrantPayload,
  dispatchToPeer,
  idempotencyKey,
  isTerminal,
  NOTE_MAX_CHARS,
  newRoomLinkRequest,
  parseRoomLinkGrant,
  parseRoomLinkRequest,
  pollPeerRun,
  postMailbox,
  takeMailbox,
  type RoomLinkGrant,
} from "./roomLinkHandshake";

afterEach(() => vi.clearAllMocks());

const HEX = "0123456789abcdef0123456789abcdef";
const DIGEST = "a".repeat(64);
const SELF = { authority_gateway_id: `install:${HEX}`, lan_ip: "10.0.0.5" };

/** 照 hermes issue_room_grant 的编码: base64url(JSON, 去 =) . base64url(签名) */
function fakeGrant(overrides: Record<string, unknown> = {}): string {
  const payload = {
    version: 2,
    grant_id: "grant-1",
    room_id: "catfish-room-1",
    home_install_id: `install:${HEX}`,
    authority_gateway_id: `install:${HEX}`,
    authority_epoch: 1,
    member_id: "member-1",
    target_install_id: "install:ffffffffffffffffffffffffffffffff",
    target_profile: "default",
    execution_policy_digest: DIGEST,
    permissions: ["dispatch", "status"],
    issued_at: 1,
    expires_at: 3600,
    ...overrides,
  };
  const b64url = (s: string) =>
    btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${b64url(JSON.stringify(payload))}.${b64url("sig")}`;
}

function grantMessage(overrides: Partial<RoomLinkGrant> = {}): RoomLinkGrant {
  return {
    v: 1,
    room_id: "catfish-room-1",
    grant: fakeGrant(),
    target_profile: "default",
    catalog: { catalog_digest: "b".repeat(64) },
    target_url: "http://10.0.0.7:8642",
    expires_at: 3600,
    ...overrides,
  };
}

const json = (status: number, body: unknown) =>
  ({ ok: status < 400, status, json: async () => body }) as Response;

// ─── 邮筒 ──────────────────────────────

describe("邮筒", () => {
  it("投递: POST central, body {to,kind,payload}", async () => {
    fetchWithAuth.mockResolvedValue(json(201, { id: 1 }));
    await postMailbox("bob@x.com", "request", "opaque");
    const [url, init] = fetchWithAuth.mock.calls[0];
    expect(url).toBe("http://central:8999/api/room-link/mailbox");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ to: "bob@x.com", kind: "request", payload: "opaque" });
  });

  it("投递失败要抛 (员工点了按钮)", async () => {
    fetchWithAuth.mockResolvedValue(json(503, {}));
    await expect(postMailbox("bob@x.com", "grant", "p")).rejects.toThrow("503");
  });

  it("取件: skipReauth, 坏形态过滤, 网络错返空", async () => {
    fetchWithAuth.mockResolvedValue(
      json(200, {
        messages: [
          { id: 1, from: "a@x", kind: "request", payload: "p", created_at: "t" },
          { id: 2, from: "a@x", kind: "chat", payload: "p", created_at: "t" },
          null,
          { id: 3, from: 7, kind: "grant", payload: "p" },
        ],
      }),
    );
    const got = await takeMailbox();
    expect(got.map((m) => m.id)).toEqual([1]);
    expect(fetchWithAuth.mock.calls[0][2]).toEqual({ skipReauth: true });

    fetchWithAuth.mockRejectedValue(new Error("net"));
    expect(await takeMailbox()).toEqual([]);
    fetchWithAuth.mockResolvedValue(json(200, { nope: 1 }));
    expect(await takeMailbox()).toEqual([]);
  });
});

// ─── 请求载荷 ──────────────────────────────

describe("请求载荷", () => {
  it("新请求: 两个 id 都是 install:<hex32>, epoch=1, 往返编解码一致", () => {
    const req = newRoomLinkRequest(SELF, "  帮我看下合同第三条  ");
    expect(req.home_install_id).toBe(`install:${HEX}`);
    expect(req.authority_gateway_id).toBe(`install:${HEX}`);
    expect(req.authority_epoch).toBe(1);
    expect(req.note).toBe("帮我看下合同第三条");
    expect(req.room_id).toMatch(/^catfish-room-/);
    expect(parseRoomLinkRequest(JSON.stringify(req))).toEqual(req);
  });

  it("没 install_id / 说明空或超长 → 抛", () => {
    expect(() => newRoomLinkRequest({ authority_gateway_id: null, lan_ip: null }, "x")).toThrow(
      "install_id",
    );
    expect(() => newRoomLinkRequest(SELF, "   ")).toThrow();
    expect(() => newRoomLinkRequest(SELF, "x".repeat(NOTE_MAX_CHARS + 1))).toThrow();
  });

  it("解请求: 坏 JSON / 版本 / 非法标识符 / epoch=0 → null", () => {
    const good = newRoomLinkRequest(SELF, "n");
    expect(parseRoomLinkRequest("{")).toBeNull();
    expect(parseRoomLinkRequest(JSON.stringify({ ...good, v: 2 }))).toBeNull();
    expect(parseRoomLinkRequest(JSON.stringify({ ...good, room_id: "-bad" }))).toBeNull();
    expect(parseRoomLinkRequest(JSON.stringify({ ...good, room_id: "has space" }))).toBeNull();
    expect(parseRoomLinkRequest(JSON.stringify({ ...good, authority_epoch: 0 }))).toBeNull();
    expect(parseRoomLinkRequest(JSON.stringify({ ...good, authority_epoch: "1" }))).toBeNull();
    expect(parseRoomLinkRequest(JSON.stringify({ ...good, note: 1 }))).toBeNull();
  });

  it("解请求: 多余字段被丢掉 (不把同事塞的东西原样带进 UI)", () => {
    const good = newRoomLinkRequest(SELF, "n");
    const parsed = parseRoomLinkRequest(JSON.stringify({ ...good, extra: "<script>" }));
    expect(parsed).toEqual(good);
  });
});

// ─── 授权载荷 ──────────────────────────────

describe("授权载荷", () => {
  it("往返一致", () => {
    const g = grantMessage();
    expect(parseRoomLinkGrant(JSON.stringify(g))).toEqual(g);
  });

  it("坏 grant / 坏 digest / 坏 target_url → null", () => {
    const g = grantMessage();
    const bad = (o: Partial<RoomLinkGrant>) => parseRoomLinkGrant(JSON.stringify({ ...g, ...o }));
    expect(bad({ grant: "no-dot" })).toBeNull();
    expect(bad({ catalog: { catalog_digest: "zz" } })).toBeNull();
    expect(bad({ target_url: "ftp://x" })).toBeNull();
    expect(bad({ target_url: "http://10.0.0.7:8642/v1" })).toBeNull();
    expect(bad({ target_url: "http://10.0.0.7:8642?x=1" })).toBeNull();
    expect(bad({ expires_at: "soon" as unknown as number })).toBeNull();
  });
});

// ─── dispatch ──────────────────────────────

describe("dispatch 组装", () => {
  it("decodeGrantPayload: base64url 无 padding 也能解", () => {
    expect(decodeGrantPayload(fakeGrant({ room_id: "r/x:y@z" })).room_id).toBe("r/x:y@z");
  });

  it("17 个字段齐, 8 个坐标抄 grant, digest 抄 catalog, prompt_digest 是 sha256", async () => {
    const g = grantMessage();
    const d = await buildDispatch(g, "  写一段周报  ");
    expect(Object.keys(d).sort()).toEqual(
      [
        "protocol_version", "room_id", "home_install_id", "authority_gateway_id",
        "authority_epoch", "member_id", "target_install_id", "target_profile",
        "task_id", "execution_generation", "source_event_seq", "cancellation_scope_id",
        "prompt", "prompt_digest", "capability_digest", "execution_policy_digest", "trace_id",
      ].sort(),
    );
    const p = decodeGrantPayload(g.grant);
    for (const f of [
      "room_id", "home_install_id", "authority_gateway_id", "authority_epoch",
      "member_id", "target_install_id", "target_profile", "execution_policy_digest",
    ] as const) {
      expect(d[f]).toBe(p[f]);
    }
    expect(d.protocol_version).toBe(2);
    expect(d.capability_digest).toBe("b".repeat(64));
    expect(d.prompt).toBe("写一段周报");
    // sha256("写一段周报") 独立算一遍
    const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode("写一段周报"));
    const hex = Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");
    expect(d.prompt_digest).toBe(hex);
    expect(d.execution_generation).toBe(1);
    expect(d.source_event_seq).toBe(1);
    for (const f of ["task_id", "cancellation_scope_id", "trace_id"] as const) {
      expect(d[f]).toMatch(/^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$/);
    }
    expect(idempotencyKey(d)).toBe(`room:${d.task_id}:1`);
  });

  it("grant 缺坐标 / room_id 跟信不符 / prompt 空 → 抛", async () => {
    await expect(
      buildDispatch(grantMessage({ grant: fakeGrant({ member_id: undefined }) }), "p"),
    ).rejects.toThrow("member_id");
    await expect(
      buildDispatch(grantMessage({ grant: fakeGrant({ execution_policy_digest: "x" }) }), "p"),
    ).rejects.toThrow("execution_policy_digest");
    await expect(
      buildDispatch(grantMessage({ room_id: "catfish-room-other" }), "p"),
    ).rejects.toThrow("room_id");
    await expect(buildDispatch(grantMessage(), "   ")).rejects.toThrow("prompt");
  });

  it("两次组装 task_id 不同 (幂等键不能撞)", async () => {
    const g = grantMessage();
    const a = await buildDispatch(g, "p");
    const b = await buildDispatch(g, "p");
    expect(a.task_id).not.toBe(b.task_id);
  });
});

// ─── 打 B ──────────────────────────────

describe("打 B 的 hermes", () => {
  it("派工: 走代理, HermesRoom 头, Idempotency-Key, body 只有 input+hosted_room_dispatch", async () => {
    const g = grantMessage();
    const d = await buildDispatch(g, "p");
    fetchViaProxy.mockResolvedValue(json(200, { run_id: "run_1", status: "started" }));
    expect(await dispatchToPeer(g, d)).toBe("run_1");
    const [url, init] = fetchViaProxy.mock.calls[0];
    expect(url).toBe("http://10.0.0.7:8642/v1/runs");
    expect(init.headers.Authorization).toBe(`HermesRoom ${g.grant}`);
    expect(init.headers["Idempotency-Key"]).toBe(`room:${d.task_id}:1`);
    const body = JSON.parse(init.body);
    expect(Object.keys(body).sort()).toEqual(["hosted_room_dispatch", "input"]);
    expect(body.input).toBe(d.prompt);
    expect(body.hosted_room_dispatch).toEqual(d);
    expect(fetchWithAuth).not.toHaveBeenCalled();
  });

  it("派工被拒: 带 hermes 的错误信息", async () => {
    const g = grantMessage();
    const d = await buildDispatch(g, "p");
    fetchViaProxy.mockResolvedValue(
      json(403, { error: { message: "Room capability catalog changed; reauthorization is required.", code: "x" } }),
    );
    await expect(dispatchToPeer(g, d)).rejects.toThrow("reauthorization");
  });

  it("轮询: GET /v1/runs/{id} 带 HermesRoom, 映射 status/output/error", async () => {
    const g = grantMessage();
    fetchViaProxy.mockResolvedValue(json(200, { status: "completed", output: "done", usage: {} }));
    expect(await pollPeerRun(g, "run 1")).toEqual({ status: "completed", output: "done", error: undefined });
    const [url, init] = fetchViaProxy.mock.calls[0];
    expect(url).toBe("http://10.0.0.7:8642/v1/runs/run%201");
    expect(init.headers.Authorization).toBe(`HermesRoom ${g.grant}`);

    fetchViaProxy.mockResolvedValue(json(200, { status: "failed", error: "outbound_declined" }));
    expect((await pollPeerRun(g, "r")).error).toBe("outbound_declined");

    fetchViaProxy.mockResolvedValue(json(404, {}));
    await expect(pollPeerRun(g, "r")).rejects.toThrow("404");
  });

  it("终态判定", () => {
    expect(isTerminal("completed")).toBe(true);
    expect(isTerminal("failed")).toBe(true);
    expect(isTerminal("waiting_for_approval")).toBe(false);
    expect(isTerminal("running")).toBe(false);
  });
});
