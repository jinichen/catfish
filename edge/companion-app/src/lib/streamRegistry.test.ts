/**
 * P3.5.30 (6/17 鸿波) — streamRegistry 真**修 P3.5.21 原 bug** (切走 chat 再回来不见 final).
 *
 * 跑法: cd edge/companion-app && npm exec vitest run streamRegistry
 *
 * 覆盖:
 * - start / update / get / finish chain
 * - finish() 真**不立即 delete** (5/24 老行为) — keep messages
 * - dismiss() 真**ChatTab restore 后立即清**
 * - 60 秒兜底 TTL — vi.useFakeTimers 模拟
 * - subscribe 真**实时 sync** (stream 中 切回 ChatTab 用)
 * - isInflight / getInflightSessions 真**finish 后**真**返 false / 不在 list**
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as registry from "./streamRegistry";
import type { ChatMessage } from "../types/chat";

const _msg = (id: string, content: string): ChatMessage => ({
  id,
  role: "assistant",
  content,
  ts: new Date().toISOString(),
  status: "streaming",
});

beforeEach(() => {
  registry._abortAllForReset();
  vi.useFakeTimers();
});

afterEach(() => {
  registry._abortAllForReset();
  vi.useRealTimers();
});

describe("start / get / update — stream 中镜像 messages", () => {
  it("start 真**初始 messages 真 copy**, get 真**拿同一 sessionId 真 state**", () => {
    const init: ChatMessage[] = [_msg("u1", "hi")];
    registry.start("sess-A", "model-X", init);
    const s = registry.get("sess-A");
    expect(s).toBeDefined();
    expect(s!.sessionId).toBe("sess-A");
    expect(s!.model).toBe("model-X");
    expect(s!.isStreaming).toBe(true);
    expect(s!.messages).toHaveLength(1);
    expect(s!.messages[0].id).toBe("u1");
  });

  it("update mutator 真**改 messages**, get 真**拿到新值**", () => {
    registry.start("sess-A", "model-X", [_msg("u1", "hi")]);
    registry.update("sess-A", (s) => {
      s.messages.push(_msg("a1", "hello"));
      s.streamingId = "a1";
    });
    const s = registry.get("sess-A");
    expect(s!.messages).toHaveLength(2);
    expect(s!.messages[1].id).toBe("a1");
    expect(s!.streamingId).toBe("a1");
  });

  it("isInflight 真 stream 中 true, finish 后 false", () => {
    registry.start("sess-A", "model-X", []);
    expect(registry.isInflight("sess-A")).toBe(true);
    registry.finish("sess-A");
    expect(registry.isInflight("sess-A")).toBe(false);
  });
});

describe("finish() 真**P3.5.30 新行为**: 不立即 delete, 留 messages", () => {
  it("finish 后真**state 还在**, isStreaming = false, messages 保留", () => {
    registry.start("sess-A", "model-X", [_msg("u1", "hi")]);
    registry.update("sess-A", (s) => {
      s.messages.push(_msg("a1", "final"));
    });
    registry.finish("sess-A");
    const s = registry.get("sess-A");
    expect(s).toBeDefined();
    expect(s!.isStreaming).toBe(false);
    expect(s!.streamingId).toBeNull();
    expect(s!.messages).toHaveLength(2);
    expect(s!.messages[1].content).toBe("final");
  });

  it("getInflightSessions 真**finish 后**不含 finished session", () => {
    registry.start("sess-A", "model-X", []);
    registry.start("sess-B", "model-X", []);
    expect(registry.getInflightSessions().sort()).toEqual(["sess-A", "sess-B"]);
    registry.finish("sess-A");
    expect(registry.getInflightSessions()).toEqual(["sess-B"]);
  });

  it("60 秒兜底 TTL — 真**vi.advanceTimersByTime 后 state 真**清**", () => {
    registry.start("sess-A", "model-X", [_msg("u1", "hi")]);
    registry.finish("sess-A");
    expect(registry.get("sess-A")).toBeDefined();

    // 59 秒还在
    vi.advanceTimersByTime(59_000);
    expect(registry.get("sess-A")).toBeDefined();

    // 60 秒后清掉
    vi.advanceTimersByTime(2_000);
    expect(registry.get("sess-A")).toBeUndefined();
  });
});

describe("dismiss() 真**ChatTab restore 后立即清**", () => {
  it("dismiss 真**state 立即清**", () => {
    registry.start("sess-A", "model-X", [_msg("u1", "hi")]);
    registry.finish("sess-A");
    expect(registry.get("sess-A")).toBeDefined();
    registry.dismiss("sess-A");
    expect(registry.get("sess-A")).toBeUndefined();
  });

  it("dismiss 真**取消 60 秒 TTL 定时器** (无 stale delete 二次 emit)", () => {
    registry.start("sess-A", "model-X", []);
    registry.finish("sess-A");
    registry.dismiss("sess-A");

    // dismiss 后 真**新建同 sessionId 真 stream**, 真**60 秒过完不该被老 timer 杀**
    registry.start("sess-A", "model-X", [_msg("u1", "round-2")]);
    vi.advanceTimersByTime(70_000);
    const s = registry.get("sess-A");
    expect(s).toBeDefined();
    expect(s!.messages[0].content).toBe("round-2");
  });
});

describe("subscribe — stream 中切回 实时 sync", () => {
  it("subscribe 真**update / finish / dismiss 都触发 cb**", () => {
    registry.start("sess-A", "model-X", []);
    const cb = vi.fn();
    const unsub = registry.subscribe("sess-A", cb);
    registry.update("sess-A", (s) => {
      s.messages.push(_msg("a1", "delta"));
    });
    registry.finish("sess-A");
    registry.dismiss("sess-A");
    expect(cb).toHaveBeenCalledTimes(3);  // update + finish + dismiss
    unsub();
  });

  it("unsubscribe 真**不再触发**", () => {
    registry.start("sess-A", "model-X", []);
    const cb = vi.fn();
    const unsub = registry.subscribe("sess-A", cb);
    unsub();
    registry.update("sess-A", (s) => {
      s.messages.push(_msg("a1", "after-unsub"));
    });
    expect(cb).not.toHaveBeenCalled();
  });

  it("wildcard subscribeInflight 真**任何 session 真 start/finish 都触发**", () => {
    const cb = vi.fn();
    const unsub = registry.subscribeInflight(cb);
    registry.start("sess-A", "model-X", []);
    registry.start("sess-B", "model-X", []);
    registry.finish("sess-A");
    expect(cb.mock.calls.length).toBeGreaterThanOrEqual(3);
    unsub();
  });
});

describe("E2E — 真**P3.5.30 修 P3.5.21 原 bug 真完整 chain**", () => {
  it("stream 中切走 → 完成 → 切回 真**restore final**", () => {
    // 1. send 真**start registry**
    const init: ChatMessage[] = [_msg("u1", "你好"), _msg("a1", "")];
    registry.start("sess-A", "main", init);

    // 2. stream 中 onDelta 真**镜像 累 messages**
    registry.update("sess-A", (s) => {
      s.messages = [
        _msg("u1", "你好"),
        { ..._msg("a1", "你好! 我是"), status: "streaming" },
      ];
      s.streamingId = "a1";
    });

    // 3. 用户切走 tab (ChatTab unmount, useChat closure 继续)
    // 4. stream 真**完成 — onDone 镜像 final + finally finish()
    registry.update("sess-A", (s) => {
      s.messages = [
        _msg("u1", "你好"),
        { ..._msg("a1", "你好! 我是小鲶."), status: "done" },
      ];
      s.streamingId = null;
    });
    registry.finish("sess-A");

    // 5. 真**关键** — 用户切回 Chat tab (ChatTab mount restore)
    const restored = registry.get("sess-A");
    expect(restored).toBeDefined();
    expect(restored!.isStreaming).toBe(false);
    expect(restored!.messages).toHaveLength(2);
    expect(restored!.messages[1].content).toBe("你好! 我是小鲶.");

    // 6. ChatTab restore 后 dismiss 清
    registry.dismiss("sess-A");
    expect(registry.get("sess-A")).toBeUndefined();
  });
});
