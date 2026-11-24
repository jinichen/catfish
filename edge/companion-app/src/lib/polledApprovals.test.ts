// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

const fetchViaProxy = vi.fn();
vi.mock("./http_proxy", () => ({ fetchViaProxy: (...a: unknown[]) => fetchViaProxy(...a) }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(async () => undefined) }));

import { newDetails, parseQueueKey, queueKey } from "./polledApprovals";
import { nativeApprovalFor, registerNativeApproval } from "./nativeApprovals";
import { toolBridgeChatApproval } from "./tauri_services";
import { _resetPolled, syncPolledApprovals } from "../hooks/usePolledApprovals";
import { useApprovalsStore } from "../store/approvals";

const SID = "20261106_142422_babcd2";
const KEY = `agent:main:api_server:dm:${SID}`;
const Q = { session_key: KEY, session_id: SID, request_id: "1e8d1efd", command: "execute_code <<'PY'\nimport docx", description: "⚠ 每次都要确认" };
const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) });

beforeEach(() => {
  fetchViaProxy.mockReset();
  _resetPolled();
  useApprovalsStore.setState({ items: [], notes: [], preapprovals: [], submitting: {} } as never);
});

describe("窗口没连着那一轮时的批准 (11/24 授权卡片没弹)", () => {
  it("队列里那种的 key 能拆回 session_key / request_id", () => {
    expect(parseQueueKey(queueKey(KEY, "1e8d1efd"))).toEqual({ sessionKey: KEY, requestId: "1e8d1efd" });
    expect(parseQueueKey("run-abc")).toBeNull();
  });

  it("只放这个对话的; 流里有 run_id 的登记成原生审批; 已经顺着流来的不弹第二张", () => {
    const run = { run_id: "run-7", session_id: SID, request_id: "r7", execution_generation: 3, command: "curl" };
    const other = { ...Q, session_id: "别的对话", request_id: "x" };
    const got = newDetails(SID, [Q, run, other], new Set());
    expect(got.map((d) => d.approval_session_key)).toEqual([queueKey(KEY, "1e8d1efd"), "run-7"]);
    expect(nativeApprovalFor("run-7")).toEqual({ requestId: "r7", generation: 3 });
    registerNativeApproval("run-9", "1e8d1efd", 62);
    expect(newDetails(SID, [Q], new Set())).toEqual([]);
  });

  it("同步: 新的放进横幅, 不在等了的收掉; 重复轮询不重置倒计时", async () => {
    fetchViaProxy.mockResolvedValueOnce(ok({ approvals: [{ ...Q, request_id: "aaa" }] }));
    await syncPolledApprovals(SID);
    const first = useApprovalsStore.getState().items;
    expect(first.map((i) => i.key)).toEqual([queueKey(KEY, "aaa")]);
    fetchViaProxy.mockResolvedValueOnce(ok({ approvals: [{ ...Q, request_id: "aaa" }] }));
    await syncPolledApprovals(SID);
    expect(useApprovalsStore.getState().items[0]).toBe(first[0]);
    fetchViaProxy.mockResolvedValueOnce(ok({ approvals: [] }));
    await syncPolledApprovals(SID);
    expect(useApprovalsStore.getState().items).toEqual([]);
  });

  it("点批准: 带 request_id 只批这一条; 已经不在等了按作废报", async () => {
    fetchViaProxy.mockResolvedValueOnce(ok({ resolved: 1, choice: "once" }));
    await toolBridgeChatApproval(queueKey(KEY, "aaa"), "once");
    const [url, init] = fetchViaProxy.mock.calls[0];
    expect(url).toContain(`/v1/sessions/${encodeURIComponent(KEY)}/approval`);
    expect(JSON.parse(init.body)).toEqual({ choice: "once", request_id: "aaa" });
    fetchViaProxy.mockResolvedValueOnce(ok({ resolved: 0, choice: "once" }));
    await expect(toolBridgeChatApproval(queueKey(KEY, "aaa"), "once")).rejects.toThrow(/409/);
  });
});
