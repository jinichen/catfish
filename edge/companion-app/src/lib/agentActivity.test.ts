/**
 * agentActivity —— 进度快照读取端的判据。
 *
 * 重点不在"能不能拉到", 在**拉不到的时候说的是哪一种拉不到**:
 * "hermes 说现在没有正在跑的 turn" 和 "我们够不着 hermes" 在 UI 上的处理完全
 * 不同 (前者正常, 后者要退回盲等), 糊在一起就没法排查。
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  PROBE_UNREACHABLE,
  describeTurn,
  fetchAgentActivity,
  pickPrimaryTurn,
  startActivityPolling,
} from "./agentActivity";

vi.mock("./env", () => ({ config: { backendUrl: "http://127.0.0.1:8642" } }));

const fetchWithAuth = vi.fn();
vi.mock("./me", () => ({
  fetchWithAuth: (...args: unknown[]) => fetchWithAuth(...args),
}));

afterEach(() => {
  fetchWithAuth.mockReset();
  vi.useRealTimers();
});

function okResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body };
}

describe("fetchAgentActivity", () => {
  it("正常返回原样透出", async () => {
    fetchWithAuth.mockResolvedValue(
      okResponse({
        available: true,
        turns: [{ session_key: "api-1", current_tool: "read_file" }],
      }),
    );
    const out = await fetchAgentActivity();
    expect(out.available).toBe(true);
    expect(out.turns[0].current_tool).toBe("read_file");
  });

  it("网络炸了返 probe_unreachable，不抛", async () => {
    fetchWithAuth.mockRejectedValue(new Error("ECONNREFUSED"));
    const out = await fetchAgentActivity();
    expect(out.available).toBe(false);
    expect(out.reason).toBe(PROBE_UNREACHABLE);
    expect(out.turns).toEqual([]);
  });

  it("HTTP 非 2xx 带上状态码 —— 401 和连不上要分得开", async () => {
    fetchWithAuth.mockResolvedValue({ ok: false, status: 401, json: async () => ({}) });
    expect((await fetchAgentActivity()).reason).toBe("http_401");
  });

  it("返回体形状不对返 bad_shape，不硬当成空结果", async () => {
    fetchWithAuth.mockResolvedValue(okResponse({ available: true }));
    expect((await fetchAgentActivity()).reason).toBe("bad_shape");
  });

  it("hermes 说没有正在跑的 turn ≠ 我们够不着 hermes", async () => {
    fetchWithAuth.mockResolvedValue(
      okResponse({ available: false, reason: "no_running_turn", turns: [] }),
    );
    const out = await fetchAgentActivity();
    expect(out.reason).toBe("no_running_turn");
    expect(out.reason).not.toBe(PROBE_UNREACHABLE);
  });
});

describe("pickPrimaryTurn", () => {
  it("空数组返 null", () => {
    expect(pickPrimaryTurn([])).toBeNull();
  });

  it("取最近动过的那条", () => {
    const picked = pickPrimaryTurn([
      { session_key: "old", last_activity_at: 100 },
      { session_key: "new", last_activity_at: 200 },
    ]);
    expect(picked?.session_key).toBe("new");
  });

  it("没有时间戳就退回第一条，不假装排过序", () => {
    const picked = pickPrimaryTurn([{ session_key: "a" }, { session_key: "b" }]);
    expect(picked?.session_key).toBe("a");
  });
});

describe("describeTurn", () => {
  it("null 返空串 —— 兜底文案归 UI，不在这里编", () => {
    expect(describeTurn(null)).toBe("");
    expect(describeTurn({})).toBe("");
  });

  it("有工具名时优先说工具", () => {
    expect(describeTurn({ current_tool: "grep", api_call_count: 2 })).toBe(
      "正在用 grep · 第 2 轮",
    );
  });

  it("没工具名才退回 description", () => {
    expect(describeTurn({ last_activity_description: "压缩上下文中" })).toBe(
      "压缩上下文中",
    );
  });

  it("有 max_iterations 就显示分母", () => {
    expect(describeTurn({ api_call_count: 3, max_iterations: 40 })).toBe("第 3/40 轮");
  });

  it("卡住超过 30 秒才提，免得正常等待也刷这句", () => {
    expect(describeTurn({ current_tool: "bash", seconds_since_activity: 5 })).toBe(
      "正在用 bash",
    );
    expect(describeTurn({ current_tool: "bash", seconds_since_activity: 45 })).toBe(
      "正在用 bash · 已 45 秒没动静",
    );
  });
});

describe("startActivityPolling", () => {
  it("停了之后不再回调 —— 组件卸载后不该继续打", async () => {
    vi.useFakeTimers();
    fetchWithAuth.mockResolvedValue(okResponse({ available: true, turns: [] }));
    const onUpdate = vi.fn();

    const stop = startActivityPolling(onUpdate, 1000);
    await vi.advanceTimersByTimeAsync(0);
    const callsBefore = onUpdate.mock.calls.length;
    expect(callsBefore).toBeGreaterThan(0);

    stop();
    await vi.advanceTimersByTimeAsync(5000);
    expect(onUpdate.mock.calls.length).toBe(callsBefore);
  });

  it("用 setTimeout 链，慢响应不会堆叠重复请求", async () => {
    vi.useFakeTimers();
    let resolveIt: (v: unknown) => void = () => {};
    fetchWithAuth.mockImplementation(
      () => new Promise((r) => { resolveIt = r; }),
    );

    const stop = startActivityPolling(vi.fn(), 100);
    await vi.advanceTimersByTimeAsync(1000);   // 响应一直不回
    expect(fetchWithAuth).toHaveBeenCalledTimes(1);  // 没有第 2 次

    resolveIt(okResponse({ available: true, turns: [] }));
    stop();
  });
});
