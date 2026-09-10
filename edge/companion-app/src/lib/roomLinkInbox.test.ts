/**
 * roomLinkInbox.ts —— 邮筒轮询的分拣 + B 侧「同意」的请求形态。
 *
 * 盯三件事:
 *   1. 轮询把 request / grant 分开, 解析不了的丢, 不抛
 *   2. 同意: 调本机 hermes invitations 的 body 恰好是 hermes 要的 5 个坐标 + ttl,
 *      投回邮筒的 grant 信包含 hermes 原样返回 + 我的内网地址
 *   3. hermes 拒签 / 没内网地址 → 抛且**不投信** (不能把半张授权发出去)
 */
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./env", () => ({
  config: { gatewayUrl: "http://central:8999", backendUrl: "http://127.0.0.1:8642" },
}));

const fetchWithAuth = vi.fn();
vi.mock("./me", () => ({ fetchWithAuth: (...a: unknown[]) => fetchWithAuth(...a) }));

const postMailbox = vi.fn();
const takeMailbox = vi.fn();
const fetchLocalIdentity = vi.fn();
vi.mock("./roomLinkHandshake", async (importOriginal) => {
  const real = await importOriginal<typeof import("./roomLinkHandshake")>();
  return {
    ...real,
    postMailbox: (...a: unknown[]) => postMailbox(...a),
    takeMailbox: (...a: unknown[]) => takeMailbox(...a),
    fetchLocalIdentity: (...a: unknown[]) => fetchLocalIdentity(...a),
  };
});

import {
  approveInboxRequest,
  GRANT_TTL_SECONDS,
  hermesPortFrom,
  startMailboxPolling,
  type InboxRequest,
} from "./roomLinkInbox";

afterEach(() => vi.clearAllMocks());

const HEX = "0123456789abcdef0123456789abcdef";
const REQUEST = {
  v: 1,
  room_id: "catfish-room-1",
  home_install_id: `install:${HEX}`,
  authority_gateway_id: `install:${HEX}`,
  authority_epoch: 1,
  member_id: "member-1",
  note: "帮我看下合同",
};
const GRANT_MSG = {
  v: 1,
  room_id: "catfish-room-1",
  grant: "aGVhZA.c2ln",
  target_profile: "default",
  catalog: { catalog_digest: "b".repeat(64) },
  target_url: "http://10.0.0.7:8642",
  expires_at: 3600,
};

const msg = (id: number, kind: string, payload: unknown) => ({
  id,
  from: "alice@x.com",
  kind,
  payload: typeof payload === "string" ? payload : JSON.stringify(payload),
  created_at: "2026-09-10T00:00:00+00:00",
});

describe("邮筒轮询分拣", () => {
  it("request / grant 各归各, 坏的丢掉", async () => {
    takeMailbox.mockResolvedValueOnce([
      msg(1, "request", REQUEST),
      msg(2, "grant", GRANT_MSG),
      msg(3, "request", "{not json"),
      msg(4, "request", { ...REQUEST, authority_epoch: 0 }),
      msg(5, "grant", { ...GRANT_MSG, grant: "nodot" }),
    ]);
    takeMailbox.mockResolvedValue([]);
    const onRequests = vi.fn();
    const onGrants = vi.fn();
    const stop = startMailboxPolling(onRequests, onGrants, 100_000);
    await vi.waitFor(() => expect(onRequests).toHaveBeenCalled());
    stop();
    expect(onRequests.mock.calls[0][0]).toEqual([
      { id: 1, from: "alice@x.com", received_at: "2026-09-10T00:00:00+00:00", request: REQUEST },
    ]);
    expect(onGrants.mock.calls[0][0]).toEqual([
      { id: 2, from: "alice@x.com", received_at: "2026-09-10T00:00:00+00:00", grant: GRANT_MSG },
    ]);
  });

  it("空轮询不回调", async () => {
    takeMailbox.mockResolvedValue([]);
    const onRequests = vi.fn();
    const onGrants = vi.fn();
    const stop = startMailboxPolling(onRequests, onGrants, 100_000);
    await vi.waitFor(() => expect(takeMailbox).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 10));
    stop();
    expect(onRequests).not.toHaveBeenCalled();
    expect(onGrants).not.toHaveBeenCalled();
  });
});

describe("同意同事的请求", () => {
  const item: InboxRequest = { id: 1, from: "alice@x.com", received_at: "t", request: REQUEST };
  const invitation = {
    object: "hermes.room_member.invitation",
    grant: "aGVhZA.c2ln",
    target_profile: "default",
    catalog: { catalog_digest: "b".repeat(64), installation_id: "install:ff" },
    expires_at: 1700000000,
    status_expires_at: 1700000000,
  };
  const json = (status: number, body: unknown) =>
    ({ ok: status < 400, status, json: async () => body }) as Response;

  it("调本机 hermes 的 body 恰好 5 坐标 + ttl; 投回的 grant 信带我的地址", async () => {
    fetchWithAuth.mockResolvedValue(json(201, invitation));
    fetchLocalIdentity.mockResolvedValue({ authority_gateway_id: "install:ff", lan_ip: "10.0.0.7" });
    postMailbox.mockResolvedValue(undefined);

    await approveInboxRequest(item);

    const [url, init] = fetchWithAuth.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8642/v1/room-members/invitations");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      room_id: "catfish-room-1",
      home_install_id: `install:${HEX}`,
      authority_gateway_id: `install:${HEX}`,
      authority_epoch: 1,
      member_id: "member-1",
      ttl_seconds: GRANT_TTL_SECONDS,
    });

    expect(postMailbox).toHaveBeenCalledTimes(1);
    const [to, kind, payload] = postMailbox.mock.calls[0];
    expect(to).toBe("alice@x.com");
    expect(kind).toBe("grant");
    expect(JSON.parse(payload)).toEqual({
      v: 1,
      room_id: "catfish-room-1",
      grant: "aGVhZA.c2ln",
      target_profile: "default",
      catalog: invitation.catalog,
      target_url: "http://10.0.0.7:8642",
      expires_at: 1700000000,
    });
  });

  it("hermes 拒签 → 抛 hermes 的话, 不投信", async () => {
    fetchWithAuth.mockResolvedValue(
      json(400, { error: { message: "remote room execution requires manual or smart approvals" } }),
    );
    await expect(approveInboxRequest(item)).rejects.toThrow("approvals");
    expect(postMailbox).not.toHaveBeenCalled();
  });

  it("没内网地址 → 抛, 不投信", async () => {
    fetchWithAuth.mockResolvedValue(json(201, invitation));
    fetchLocalIdentity.mockResolvedValue({ authority_gateway_id: "install:ff", lan_ip: null });
    await expect(approveInboxRequest(item)).rejects.toThrow("内网地址");
    expect(postMailbox).not.toHaveBeenCalled();
  });

  it("端口跟 backendUrl 走", () => {
    expect(hermesPortFrom("http://127.0.0.1:8642")).toBe(8642);
    expect(hermesPortFrom("http://localhost:9000/x")).toBe(9000);
    expect(hermesPortFrom("http://localhost")).toBe(8642);
  });
});
