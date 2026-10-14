import { beforeEach, describe, expect, it, vi } from "vitest";

const fetchViaProxy = vi.fn();
vi.mock("./http_proxy", () => ({ fetchViaProxy: (...a: unknown[]) => fetchViaProxy(...a) }));
vi.mock("./env", () => ({
  config: { backendUrl: "http://127.0.0.1:8642", useHermes: true, hermesAuthHeader: "Bearer k" },
}));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

import { nativeApprovalFor, pendingFromNativeEvent, NATIVE_APPROVAL_TIMEOUT_S } from "./nativeApprovals";
import { toolBridgeChatApproval } from "./tauri_services";

const ok = () => ({ ok: true, json: async () => ({ resolved: 1 }), text: async () => "" });

describe("hermes v0.22 原生审批 (approval.request)", () => {
  beforeEach(() => fetchViaProxy.mockReset().mockResolvedValue(ok()));

  it("事件转成审批卡认的格式, run_id 当 key, 倒计时 300 秒", () => {
    const detail = pendingFromNativeEvent({
      event: "approval.request", run_id: "chatcmpl-1", request_id: "r1",
      command: "rm -rf /tmp/x", description: "recursive delete", pattern_key: "rm_rf",
      choices: ["once", "deny"],
    });
    expect(detail).toEqual({
      approval_session_key: "chatcmpl-1", command: "rm -rf /tmp/x",
      description: "recursive delete", pattern_key: "rm_rf", timeout_s: NATIVE_APPROVAL_TIMEOUT_S,
    });
    expect(nativeApprovalFor("chatcmpl-1")).toEqual({ requestId: "r1" });
  });

  it("没有 run_id 的不认", () => {
    expect(pendingFromNativeEvent({ command: "x" })).toBeNull();
    expect(pendingFromNativeEvent("garbage")).toBeNull();
  });

  it("原生审批点按钮走 /v1/runs/{id}/approval, 带上 request_id; 用过就忘", async () => {
    pendingFromNativeEvent({ run_id: "chatcmpl-2", request_id: "r2" });
    await toolBridgeChatApproval("chatcmpl-2", "once");
    const [url, init] = fetchViaProxy.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8642/v1/runs/chatcmpl-2/approval");
    expect(JSON.parse(init.body)).toEqual({ choice: "once", request_id: "r2" });
    expect(init.headers.Authorization).toBe("Bearer k");
    expect(nativeApprovalFor("chatcmpl-2")).toBeNull();
  });

  it("没登记过的 (RoomLink / 老版本插件那套) 仍走 /v1/sessions/{key}/approval", async () => {
    await toolBridgeChatApproval("sess-legacy", "deny");
    const [url, init] = fetchViaProxy.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8642/v1/sessions/sess-legacy/approval");
    expect(JSON.parse(init.body)).toEqual({ choice: "deny" });
  });
});
