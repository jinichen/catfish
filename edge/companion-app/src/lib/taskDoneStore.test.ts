/**
 * taskDoneStore.ts —— 后台任务未读计数。
 *
 * 盯的判据:
 *   1. 只算已结束 (completed/failed) 且非测试任务; 坏行丢掉不抛
 *   2. markSeen 之后到此刻的都清零, 之后新完成的又算未读; seen_ts 落 localStorage
 *   3. 两个订阅者只起一个轮询, 最后一个走了就停; tool-bridge 挂了计数不变
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const toolBridgeCallTool = vi.fn();
vi.mock("./tauri", () => ({
  toolBridgeCallTool: (...a: unknown[]) => toolBridgeCallTool(...a),
}));

// ── 假 localStorage (跟 me.test.ts 同款) ──
// Node 22 自带一个实验性 localStorage 全局, 没 --localstorage-file 时只是个空壳
// (clear/getItem 都不是函数), 还会盖住 jsdom 的。直接替掉最省事。
const _store = new Map<string, string>();
(globalThis as unknown as { localStorage: Storage }).localStorage = {
  length: 0,
  getItem: (k: string) => _store.get(k) ?? null,
  setItem: (k: string, v: string) => { _store.set(k, v); },
  removeItem: (k: string) => { _store.delete(k); },
  clear: () => _store.clear(),
  key: () => null,
};

import {
  _resetForTest,
  getSnapshot,
  isTestTask,
  markSeen,
  pickFinished,
  subscribe,
  TASK_DONE_POLL_INTERVAL_MS,
  unseenCount,
} from "./taskDoneStore";

const task = (id: string, status: string, finished_at: number, label = "写周报", kind = "execute_code") => ({
  task_id: id,
  kind,
  label,
  status,
  started_at: finished_at - 5,
  finished_at,
});

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  _resetForTest();
});
afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("pickFinished / isTestTask", () => {
  it("只留已结束 + 非测试, 新的在前, 坏行丢", () => {
    const out = pickFinished({
      tasks: [
        task("a", "completed", 100),
        task("b", "running", 200),
        task("c", "failed", 300),
        task("d", "completed", 400, "测试一下"),
        task("e", "completed", 500, "x", "unit_test"),
        { task_id: "f", status: "completed" }, // 没 finished_at
        "garbage",
      ],
    });
    expect(out.map((t) => t.task_id)).toEqual(["c", "a"]);
    expect(pickFinished(null)).toEqual([]);
    expect(pickFinished({ tasks: "nope" })).toEqual([]);
  });

  it("测试任务规则跟 tool-bridge _is_test_task 一致", () => {
    expect(isTestTask("test", "")).toBe(true);
    expect(isTestTask("foo_test", "")).toBe(true);
    expect(isTestTask("test_foo", "")).toBe(true);
    expect(isTestTask("execute_code", "测试 streaming")).toBe(true);
    expect(isTestTask("execute_code", "Test run")).toBe(true);
    expect(isTestTask("execute_code", "修订资质管理办法")).toBe(false);
  });
});

describe("未读计数", () => {
  it("订阅即拉; markSeen 清零并落盘; 之后新完成的又算", async () => {
    toolBridgeCallTool.mockResolvedValue({
      ok: true,
      result: { tasks: [task("a", "completed", 1000), task("b", "failed", 1100)] },
    });
    const stop = subscribe(() => {});
    await vi.advanceTimersByTimeAsync(0);
    expect(unseenCount()).toBe(2);
    expect(getSnapshot().unseen[0].task_id).toBe("b");

    markSeen(1200);
    expect(unseenCount()).toBe(0);
    expect(localStorage.getItem("catfish:task_done_seen_ts")).toBe("1200");

    toolBridgeCallTool.mockResolvedValue({
      ok: true,
      result: { tasks: [task("a", "completed", 1000), task("b", "failed", 1100), task("c", "completed", 1300)] },
    });
    await vi.advanceTimersByTimeAsync(TASK_DONE_POLL_INTERVAL_MS);
    expect(getSnapshot().unseen.map((t) => t.task_id)).toEqual(["c"]);
    stop();
  });

  it("seen_ts 从 localStorage 恢复 (重启不把昨天看过的标回未读)", async () => {
    localStorage.setItem("catfish:task_done_seen_ts", "1150");
    toolBridgeCallTool.mockResolvedValue({
      ok: true,
      result: { tasks: [task("a", "completed", 1000), task("b", "failed", 1100), task("c", "completed", 1300)] },
    });
    const stop = subscribe(() => {});
    await vi.advanceTimersByTimeAsync(0);
    expect(getSnapshot().unseen.map((t) => t.task_id)).toEqual(["c"]);
    stop();
  });

  it("没未读时 markSeen 不写 localStorage", () => {
    markSeen(999);
    expect(localStorage.getItem("catfish:task_done_seen_ts")).toBeNull();
  });
});

describe("轮询生命周期", () => {
  it("两个订阅者只起一个轮询, 最后一个走了就停", async () => {
    toolBridgeCallTool.mockResolvedValue({ ok: true, result: { tasks: [] } });
    const stopA = subscribe(() => {});
    const stopB = subscribe(() => {});
    await vi.advanceTimersByTimeAsync(0);
    expect(toolBridgeCallTool).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(TASK_DONE_POLL_INTERVAL_MS);
    expect(toolBridgeCallTool).toHaveBeenCalledTimes(2);
    stopA();
    await vi.advanceTimersByTimeAsync(TASK_DONE_POLL_INTERVAL_MS);
    expect(toolBridgeCallTool).toHaveBeenCalledTimes(3);
    stopB();
    await vi.advanceTimersByTimeAsync(TASK_DONE_POLL_INTERVAL_MS * 3);
    expect(toolBridgeCallTool).toHaveBeenCalledTimes(3);
  });

  it("tool-bridge 挂了 / 返 ok=false → 计数保持上一次, 不抛", async () => {
    toolBridgeCallTool.mockResolvedValueOnce({ ok: true, result: { tasks: [task("a", "completed", 1000)] } });
    const stop = subscribe(() => {});
    await vi.advanceTimersByTimeAsync(0);
    expect(unseenCount()).toBe(1);

    toolBridgeCallTool.mockRejectedValueOnce(new Error("RPC timeout"));
    await vi.advanceTimersByTimeAsync(TASK_DONE_POLL_INTERVAL_MS);
    expect(unseenCount()).toBe(1);

    toolBridgeCallTool.mockResolvedValueOnce({ ok: false, result: null, error: "tool-bridge 未启动" });
    await vi.advanceTimersByTimeAsync(TASK_DONE_POLL_INTERVAL_MS);
    expect(unseenCount()).toBe(1);
    stop();
  });

  it("内容没变不 emit (TabBar 不被 15s 一次白刷)", async () => {
    toolBridgeCallTool.mockResolvedValue({ ok: true, result: { tasks: [task("a", "completed", 1000)] } });
    const cb = vi.fn();
    const stop = subscribe(cb);
    await vi.advanceTimersByTimeAsync(0);
    expect(cb).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(TASK_DONE_POLL_INTERVAL_MS * 2);
    expect(cb).toHaveBeenCalledTimes(1);
    stop();
  });
});
