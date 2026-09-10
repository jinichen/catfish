/**
 * roomLink.ts —— 横向协同待审批的读取端 + 拍板动作。
 *
 * 跟 agentActivity.test.ts 同一套: mock 掉 fetchWithAuth, 只测本模块的判据。
 * 重点盯三件事:
 *   1. 轮询永不抛, 各种坏返回都退回 {available:false}
 *   2. 出站拍板的请求形态 (路径 / 方法 / body) 跟插件端点对得上
 *   3. 工具拍板只给 once/deny 两档 —— session/always 会把 P49.1 收回的权限送回去
 */
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./env", () => ({ config: { backendUrl: "http://127.0.0.1:8642" } }));

const fetchWithAuth = vi.fn();
vi.mock("./me", () => ({ fetchWithAuth: (...a: unknown[]) => fetchWithAuth(...a) }));

const toolBridgeChatApproval = vi.fn();
vi.mock("./tauri", () => ({
  toolBridgeChatApproval: (...a: unknown[]) => toolBridgeChatApproval(...a),
}));

import {
  fetchRoomLinkPending,
  hasPending,
  PROBE_UNREACHABLE,
  resolveRoomLinkApproval,
  resolveRoomLinkOutput,
  startRoomLinkPolling,
} from "./roomLink";

afterEach(() => {
  fetchWithAuth.mockReset();
  toolBridgeChatApproval.mockReset();
});

const okJson = (body: unknown) => ({ ok: true, status: 200, json: async () => body });

describe("fetchRoomLinkPending", () => {
  it("正常返回原样透出", async () => {
    fetchWithAuth.mockResolvedValue(okJson({
      approvals: [{ run_id: "r1", command: "ls", description: "列目录" }],
      outputs: [{ run_id: "r2", final_response: "B 的回复" }],
    }));
    const r = await fetchRoomLinkPending();
    expect(r.available).toBe(true);
    expect(r.approvals).toEqual([{ run_id: "r1", command: "ls", description: "列目录" }]);
    expect(r.outputs).toEqual([{ run_id: "r2", final_response: "B 的回复" }]);
  });

  it("走的是 hermes 的 /api/catfish/ 前缀 + skipReauth", async () => {
    fetchWithAuth.mockResolvedValue(okJson({ approvals: [], outputs: [] }));
    await fetchRoomLinkPending();
    const [url, init, opts] = fetchWithAuth.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8642/api/catfish/room-link/pending");
    expect(init.method).toBe("GET");
    // 后台探针绝不能把员工拽去登录页 —— 8/9 那次 3 秒一弹的坑
    expect(opts).toEqual({ skipReauth: true });
  });

  it("非 2xx 退回 available:false 带状态码", async () => {
    fetchWithAuth.mockResolvedValue({ ok: false, status: 503, json: async () => ({}) });
    const r = await fetchRoomLinkPending();
    expect(r).toEqual({ available: false, reason: "http_503", approvals: [], outputs: [] });
  });

  it("形状不对退回 bad_shape", async () => {
    fetchWithAuth.mockResolvedValue(okJson({ approvals: "not-a-list" }));
    expect((await fetchRoomLinkPending()).reason).toBe("bad_shape");
  });

  it("网络挂了退回 probe_unreachable, 不抛", async () => {
    fetchWithAuth.mockRejectedValue(new Error("ECONNREFUSED"));
    const r = await fetchRoomLinkPending();
    expect(r.available).toBe(false);
    expect(r.reason).toBe(PROBE_UNREACHABLE);
  });

  it("缺 run_id 的条目被过滤, 不让 UI 拿到没 key 的东西", async () => {
    fetchWithAuth.mockResolvedValue(okJson({
      approvals: [{ command: "x" }, { run_id: "ok" }],
      outputs: [{ run_id: "o1" }, { run_id: "o2", final_response: "有" }],
    }));
    const r = await fetchRoomLinkPending();
    expect(r.approvals.map((a) => a.run_id)).toEqual(["ok"]);
    expect(r.outputs.map((o) => o.run_id)).toEqual(["o2"]);
  });
});

describe("hasPending", () => {
  it("available 且任一列表非空才算有", () => {
    const base = { available: true, approvals: [], outputs: [] };
    expect(hasPending(base)).toBe(false);
    expect(hasPending({ ...base, approvals: [{ run_id: "a" }] })).toBe(true);
    expect(hasPending({ ...base, outputs: [{ run_id: "o", final_response: "x" }] })).toBe(true);
  });

  it("探不到时哪怕列表有东西也不算 —— 那是陈旧数据", () => {
    expect(hasPending({ available: false, approvals: [{ run_id: "a" }], outputs: [] })).toBe(false);
  });
});

describe("resolveRoomLinkOutput", () => {
  it("POST 到插件端点, body 是 {choice}", async () => {
    fetchWithAuth.mockResolvedValue(okJson({ ok: true }));
    const r = await resolveRoomLinkOutput("run-1", "approve");
    expect(r).toEqual({ ok: true, gone: false });
    const [url, init] = fetchWithAuth.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8642/api/catfish/room-link/outputs/run-1");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ choice: "approve" });
  });

  it("run_id 会 URL 编码", async () => {
    fetchWithAuth.mockResolvedValue(okJson({ ok: true }));
    await resolveRoomLinkOutput("a/b c", "deny");
    expect(fetchWithAuth.mock.calls[0][0]).toContain("/outputs/a%2Fb%20c");
  });

  it("404 = 已超时被 hermes 收尾, 返 gone 不抛", async () => {
    fetchWithAuth.mockResolvedValue({ ok: false, status: 404, text: async () => "" });
    expect(await resolveRoomLinkOutput("r", "approve")).toEqual({ ok: false, gone: true });
  });

  it("其它非 2xx 抛 —— 员工点了按钮, 失败得让他知道", async () => {
    fetchWithAuth.mockResolvedValue({ ok: false, status: 500, text: async () => "boom" });
    await expect(resolveRoomLinkOutput("r", "deny")).rejects.toThrow(/500.*boom/);
  });
});

describe("resolveRoomLinkApproval", () => {
  it("直接复用 P15.2 那条路, run_id 当 session_key", async () => {
    toolBridgeChatApproval.mockResolvedValue({ resolved: 1 });
    await resolveRoomLinkApproval("run-9", "once");
    expect(toolBridgeChatApproval).toHaveBeenCalledWith("run-9", "once");
  });

  it("类型上只接受 once/deny", () => {
    // session / always 会让 A 后续免审, 等于把 P49.1 收回的 approve 又送出去。
    // 这条靠 TS 签名挡, 运行时不再校验 —— 这里只钉住签名没被放宽。
    // @ts-expect-error session 不在允许的档位里
    void resolveRoomLinkApproval("r", "session");
    // @ts-expect-error always 同上
    void resolveRoomLinkApproval("r", "always");
  });
});

describe("startRoomLinkPolling", () => {
  it("停止后不再回调", async () => {
    vi.useFakeTimers();
    fetchWithAuth.mockResolvedValue(okJson({ approvals: [], outputs: [] }));
    const seen: unknown[] = [];
    const stop = startRoomLinkPolling((r) => seen.push(r), 1000);
    await vi.advanceTimersByTimeAsync(0);
    expect(seen).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(seen).toHaveLength(2);
    stop();
    await vi.advanceTimersByTimeAsync(5000);
    expect(seen).toHaveLength(2);
    vi.useRealTimers();
  });
});
