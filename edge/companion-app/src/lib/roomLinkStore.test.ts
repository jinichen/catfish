/**
 * roomLinkStore.ts —— 邮筒唯一轮询者 + 外发请求状态机。
 *
 * 盯的判据:
 *   1. 只有一个邮筒轮询 (两个订阅者不许起两个)、最后一个订阅者走了就停
 *   2. grant 只认「我在等的那个 room_id 且发件人就是我请的人」
 *   3. 收到 grant → 派工 → 轮询到终态; completed→done, 其他→failed 并带 error
 *   4. 等 grant 超过 10 分钟 → failed; 跑着的不许 dismiss
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const startMailboxPolling = vi.fn();
const approveInboxRequest = vi.fn();
vi.mock("./roomLinkInbox", () => ({
  startMailboxPolling: (...a: unknown[]) => startMailboxPolling(...a),
  approveInboxRequest: (...a: unknown[]) => approveInboxRequest(...a),
}));

const p49 = {
  startRoomLinkPolling: vi.fn(),
  resolveRoomLinkApproval: vi.fn(),
  resolveRoomLinkOutput: vi.fn(),
};
vi.mock("./roomLink", async (importOriginal) => {
  const real = await importOriginal<typeof import("./roomLink")>();
  return {
    ...real,
    startRoomLinkPolling: (...a: unknown[]) => p49.startRoomLinkPolling(...a),
    resolveRoomLinkApproval: (...a: unknown[]) => p49.resolveRoomLinkApproval(...a),
    resolveRoomLinkOutput: (...a: unknown[]) => p49.resolveRoomLinkOutput(...a),
  };
});

const handshake = {
  buildDispatch: vi.fn(),
  dispatchToPeer: vi.fn(),
  fetchLocalIdentity: vi.fn(),
  newRoomLinkRequest: vi.fn(),
  pollPeerRun: vi.fn(),
  postMailbox: vi.fn(),
};
vi.mock("./roomLinkHandshake", async (importOriginal) => {
  const real = await importOriginal<typeof import("./roomLinkHandshake")>();
  return {
    ...real,
    buildDispatch: (...a: unknown[]) => handshake.buildDispatch(...a),
    dispatchToPeer: (...a: unknown[]) => handshake.dispatchToPeer(...a),
    fetchLocalIdentity: (...a: unknown[]) => handshake.fetchLocalIdentity(...a),
    newRoomLinkRequest: (...a: unknown[]) => handshake.newRoomLinkRequest(...a),
    pollPeerRun: (...a: unknown[]) => handshake.pollPeerRun(...a),
    postMailbox: (...a: unknown[]) => handshake.postMailbox(...a),
  };
});

import {
  _resetForTest,
  dismissOutgoing,
  getSnapshot,
  GRANT_WAIT_TIMEOUT_MS,
  pendingCount,
  resolveApproval,
  resolveInbox,
  resolveOutput,
  RUN_POLL_INTERVAL_MS,
  sendRequest,
  subscribe,
} from "./roomLinkStore";

type Handlers = { onRequests: (x: unknown[]) => void; onGrants: (x: unknown[]) => void };
let handlers: Handlers;
let stopPolling: ReturnType<typeof vi.fn>;
let stopPendingPolling: ReturnType<typeof vi.fn>;
let pushPending: (p: unknown) => void;

beforeEach(() => {
  vi.useFakeTimers();
  stopPolling = vi.fn();
  stopPendingPolling = vi.fn();
  startMailboxPolling.mockImplementation((onRequests, onGrants) => {
    handlers = { onRequests, onGrants };
    return stopPolling;
  });
  p49.startRoomLinkPolling.mockImplementation((onUpdate) => {
    pushPending = onUpdate;
    return stopPendingPolling;
  });
  handshake.fetchLocalIdentity.mockResolvedValue({ authority_gateway_id: "install:a", lan_ip: "10.0.0.1" });
  handshake.newRoomLinkRequest.mockImplementation((_self, note: string) => ({
    v: 1, room_id: "catfish-room-1", home_install_id: "install:a", authority_gateway_id: "install:a",
    authority_epoch: 1, member_id: "m", note,
  }));
  handshake.postMailbox.mockResolvedValue(undefined);
  handshake.buildDispatch.mockResolvedValue({ task_id: "t", execution_generation: 1, prompt: "p" });
  handshake.dispatchToPeer.mockResolvedValue("run_1");
});

afterEach(() => {
  _resetForTest();
  vi.clearAllMocks();
  vi.useRealTimers();
});

const grantFrom = (from: string, room_id = "catfish-room-1") => ({
  id: 9, from, received_at: "t",
  grant: { v: 1, room_id, grant: "g.s", target_profile: "default", catalog: { catalog_digest: "b".repeat(64) }, target_url: "http://10.0.0.7:8642", expires_at: 1 },
});

const flush = () => vi.advanceTimersByTimeAsync(0);

describe("单例轮询", () => {
  it("两个订阅者只起一个轮询 (邮筒 + P49 探针各一); 最后一个走了才停", () => {
    const u1 = subscribe(() => {});
    const u2 = subscribe(() => {});
    expect(startMailboxPolling).toHaveBeenCalledTimes(1);
    expect(p49.startRoomLinkPolling).toHaveBeenCalledTimes(1);
    u1();
    expect(stopPolling).not.toHaveBeenCalled();
    u2();
    expect(stopPolling).toHaveBeenCalledTimes(1);
    expect(stopPendingPolling).toHaveBeenCalledTimes(1);
  });

  it("pendingCount = 同事请求 + 工具审批 + 出站回复; 探针不可用不算", () => {
    subscribe(() => {});
    expect(pendingCount()).toBe(0);
    handlers.onRequests([{ id: 1, from: "a@x", received_at: "t", request: { note: "n" } }]);
    pushPending({ available: true, approvals: [{ run_id: "r1" }], outputs: [{ run_id: "r2", final_response: "x" }, { run_id: "r3", final_response: "y" }] });
    expect(pendingCount()).toBe(4);
    pushPending({ available: false, approvals: [], outputs: [] });
    expect(pendingCount()).toBe(1);
  });

  it("resolveApproval / resolveOutput 拍完立刻从本地摘掉, 不等下一轮探针", async () => {
    subscribe(() => {});
    pushPending({ available: true, approvals: [{ run_id: "r1" }], outputs: [{ run_id: "r2", final_response: "x" }] });
    p49.resolveRoomLinkApproval.mockResolvedValue(undefined);
    await resolveApproval({ run_id: "r1" }, "once");
    expect(p49.resolveRoomLinkApproval).toHaveBeenCalledWith("r1", "once");
    expect(getSnapshot().pending?.approvals).toEqual([]);
    p49.resolveRoomLinkOutput.mockResolvedValue({ ok: false, gone: true });
    expect(await resolveOutput({ run_id: "r2", final_response: "x" }, "approve")).toMatchObject({ gone: true });
    expect(getSnapshot().pending?.outputs).toEqual([]);
  });

  it("request 进 inbox, 同 id 去重; resolveInbox approve 走 approveInboxRequest 后移除", async () => {
    subscribe(() => {});
    const item = { id: 1, from: "a@x", received_at: "t", request: { note: "n" } };
    handlers.onRequests([item]);
    handlers.onRequests([item]);
    expect(getSnapshot().inbox).toHaveLength(1);
    approveInboxRequest.mockResolvedValue(undefined);
    await resolveInbox(item as never, "approve");
    expect(approveInboxRequest).toHaveBeenCalledWith(item);
    expect(getSnapshot().inbox).toHaveLength(0);
  });

  it("approve 失败 → 抛且条目保留 (让员工重试)", async () => {
    subscribe(() => {});
    const item = { id: 1, from: "a@x", received_at: "t", request: { note: "n" } };
    handlers.onRequests([item]);
    approveInboxRequest.mockRejectedValue(new Error("hermes 拒签"));
    await expect(resolveInbox(item as never, "approve")).rejects.toThrow("拒签");
    expect(getSnapshot().inbox).toHaveLength(1);
  });
});

describe("外发请求", () => {
  it("发送: 邮箱归一化, 投 request, 进列表 waiting_grant", async () => {
    subscribe(() => {});
    await sendRequest("  Bob@X.com ", "帮我看合同");
    expect(handshake.postMailbox).toHaveBeenCalledWith("bob@x.com", "request", expect.any(String));
    const o = getSnapshot().outgoing[0];
    expect(o).toMatchObject({ room_id: "catfish-room-1", to: "bob@x.com", phase: "waiting_grant" });
  });

  it("邮箱格式不对 → 抛, 不投", async () => {
    await expect(sendRequest("bob", "x")).rejects.toThrow("邮箱");
    expect(handshake.postMailbox).not.toHaveBeenCalled();
  });

  it("grant 发件人不是我请的人 / room_id 不是我的 → 丢", async () => {
    subscribe(() => {});
    await sendRequest("bob@x.com", "x");
    handlers.onGrants([grantFrom("mallory@x.com")]);
    handlers.onGrants([grantFrom("bob@x.com", "catfish-room-other")]);
    await flush();
    expect(handshake.dispatchToPeer).not.toHaveBeenCalled();
    expect(getSnapshot().outgoing[0].phase).toBe("waiting_grant");
  });

  it("对的 grant → 派工 → running → 轮询到 completed → done + output", async () => {
    subscribe(() => {});
    await sendRequest("bob@x.com", "帮我看合同");
    handlers.onGrants([grantFrom("bob@x.com")]);
    await flush();
    expect(handshake.buildDispatch).toHaveBeenCalledWith(grantFrom("bob@x.com").grant, "帮我看合同");
    expect(getSnapshot().outgoing[0]).toMatchObject({ phase: "running", run_id: "run_1" });

    handshake.pollPeerRun.mockResolvedValueOnce({ status: "waiting_for_approval" });
    await vi.advanceTimersByTimeAsync(RUN_POLL_INTERVAL_MS);
    expect(getSnapshot().outgoing[0].run_status).toBe("waiting_for_approval");

    handshake.pollPeerRun.mockResolvedValueOnce({ status: "completed", output: "看完了" });
    await vi.advanceTimersByTimeAsync(RUN_POLL_INTERVAL_MS);
    expect(getSnapshot().outgoing[0]).toMatchObject({ phase: "done", output: "看完了" });

    // 终态后不再轮询
    handshake.pollPeerRun.mockClear();
    await vi.advanceTimersByTimeAsync(RUN_POLL_INTERVAL_MS * 3);
    expect(handshake.pollPeerRun).not.toHaveBeenCalled();
  });

  it("对方没放行 → failed, error 带 outbound_declined", async () => {
    subscribe(() => {});
    await sendRequest("bob@x.com", "x");
    handlers.onGrants([grantFrom("bob@x.com")]);
    await flush();
    handshake.pollPeerRun.mockResolvedValueOnce({ status: "failed", error: "outbound_declined" });
    await vi.advanceTimersByTimeAsync(RUN_POLL_INTERVAL_MS);
    expect(getSnapshot().outgoing[0]).toMatchObject({ phase: "failed", error: "outbound_declined" });
  });

  it("派工被拒 → failed 带对方的话", async () => {
    subscribe(() => {});
    await sendRequest("bob@x.com", "x");
    handshake.dispatchToPeer.mockRejectedValueOnce(new Error("Room capability catalog changed"));
    handlers.onGrants([grantFrom("bob@x.com")]);
    await flush();
    expect(getSnapshot().outgoing[0]).toMatchObject({ phase: "failed", error: expect.stringContaining("catalog") });
  });

  it("轮询网络抖动不改状态, 下一轮继续", async () => {
    subscribe(() => {});
    await sendRequest("bob@x.com", "x");
    handlers.onGrants([grantFrom("bob@x.com")]);
    await flush();
    handshake.pollPeerRun.mockRejectedValueOnce(new Error("net"));
    await vi.advanceTimersByTimeAsync(RUN_POLL_INTERVAL_MS);
    expect(getSnapshot().outgoing[0].phase).toBe("running");
    handshake.pollPeerRun.mockResolvedValueOnce({ status: "completed", output: "ok" });
    await vi.advanceTimersByTimeAsync(RUN_POLL_INTERVAL_MS);
    expect(getSnapshot().outgoing[0].phase).toBe("done");
  });

  it("等 grant 超 10 分钟 → failed (下一轮邮筒轮询时判)", async () => {
    subscribe(() => {});
    await sendRequest("bob@x.com", "x");
    vi.setSystemTime(Date.now() + GRANT_WAIT_TIMEOUT_MS + 1);
    handlers.onRequests([]);
    expect(getSnapshot().outgoing[0]).toMatchObject({ phase: "failed", error: expect.stringContaining("10 分钟") });
    // 超时之后再来 grant 也不派工
    handlers.onGrants([grantFrom("bob@x.com")]);
    await flush();
    expect(handshake.dispatchToPeer).not.toHaveBeenCalled();
  });

  it("dismiss: 等待中/终态可删, 跑着的不许", async () => {
    subscribe(() => {});
    await sendRequest("bob@x.com", "x");
    handlers.onGrants([grantFrom("bob@x.com")]);
    await flush();
    expect(getSnapshot().outgoing[0].phase).toBe("running");
    dismissOutgoing("catfish-room-1");
    expect(getSnapshot().outgoing).toHaveLength(1);
    handshake.pollPeerRun.mockResolvedValueOnce({ status: "cancelled" });
    await vi.advanceTimersByTimeAsync(RUN_POLL_INTERVAL_MS);
    expect(getSnapshot().outgoing[0]).toMatchObject({ phase: "failed", error: "cancelled" });
    dismissOutgoing("catfish-room-1");
    expect(getSnapshot().outgoing).toHaveLength(0);
  });
});
