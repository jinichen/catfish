/**
 * @vitest-environment jsdom
 */
/** 待办对话不跟着页面走 (11/13 鸿波: 「切到其他TAB或任务，这个任务就断了」)。
 *
 * 原来对话状态是组件里的 useState, 卸载时还主动 abort —— 员工发完一句切走,
 * 这一轮被掐断, 回来只剩自己那句。这里钉: 切走后照常跑完、照常落库, 切回来看得到;
 * 只有「停止」/「清空」才停。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";

type StreamArgs = {
  onDelta: (chunk: string) => void;
  signal: AbortSignal;
};
const streams: Array<{ args: StreamArgs; finish: () => void }> = [];
vi.mock("../lib/chat", () => ({
  streamChat: (args: StreamArgs) =>
    new Promise<void>((resolve) => {
      streams.push({ args, finish: resolve });
      args.signal.addEventListener("abort", () => resolve());
    }),
}));
vi.mock("./chat/toolsCache", () => ({ ensureTools: async () => [] }));
const appended: Array<{ sessionId: string; role: string; content: string }> = [];
vi.mock("../lib/tauri", () => ({
  toolBridgeCallTool: vi.fn(),
  sessionMessageAppend: vi.fn(async (m: { sessionId: string; role: string; content: string }) => {
    appended.push(m);
  }),
}));

import { _resetTaskChatRuntimes, isTaskChatRunning, runningTaskChatKeys, useTaskChat } from "./useTaskChat";

const opts = (chatKey: string) => ({
  chatKey,
  model: "m",
  buildSystemPrompt: () => "sys",
  ensureSessionId: async () => `sess-${chatKey}`,
});
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

beforeEach(() => { streams.length = 0; appended.length = 0; _resetTaskChatRuntimes(); });
afterEach(cleanup);

describe("useTaskChat: 切走不断", () => {
  it("发完切走 (卸载), 这一轮照常跑完并落库; 切回来看得到回答", async () => {
    const first = renderHook(() => useTaskChat(opts("task-a")));
    await act(async () => { void first.result.current.send("帮我看下这两个公告"); });
    await flush();
    expect(streams).toHaveLength(1);
    expect(first.result.current.isStreaming).toBe(true);

    first.unmount();                                   // 切到别的待办 / 别的 tab
    expect(streams[0].args.signal.aborted).toBe(false);
    expect(isTaskChatRunning("task-a")).toBe(true);
    expect(runningTaskChatKeys()).toBe("task-a");

    await act(async () => { streams[0].args.onDelta("两个公告都是成交结果。"); streams[0].finish(); });
    await flush();
    expect(isTaskChatRunning("task-a")).toBe(false);
    expect(appended.map((m) => [m.sessionId, m.role, m.content])).toEqual([
      ["sess-task-a", "user", "帮我看下这两个公告"],
      ["sess-task-a", "assistant", "两个公告都是成交结果。"],
    ]);

    const back = renderHook(() => useTaskChat(opts("task-a")));   // 切回来
    expect(back.result.current.isStreaming).toBe(false);
    expect(back.result.current.messages.map((m) => [m.role, m.content, m.status])).toEqual([
      ["user", "帮我看下这两个公告", "done"],
      ["assistant", "两个公告都是成交结果。", "done"],
    ]);
  });

  it("切回来时还在跑: 看得到进行中的回答; 库里读出来的旧历史不覆盖它", async () => {
    const first = renderHook(() => useTaskChat(opts("task-a")));
    await act(async () => { void first.result.current.send("第一句"); });
    await flush();
    first.unmount();
    await act(async () => { streams[0].args.onDelta("写到一半"); });

    const back = renderHook(() => useTaskChat(opts("task-a")));
    act(() => back.result.current.loadHistory([]));               // DetailPane 挂载时会这么调
    expect(back.result.current.isStreaming).toBe(true);
    expect(back.result.current.messages.map((m) => m.content)).toEqual(["第一句", "写到一半"]);
  });

  it("两条待办互不串: A 在跑时 B 是空的、能发", async () => {
    const a = renderHook(() => useTaskChat(opts("task-a")));
    await act(async () => { void a.result.current.send("A 的问题"); });
    await flush();
    a.unmount();
    const b = renderHook(() => useTaskChat(opts("task-b")));
    expect(b.result.current.messages).toEqual([]);
    expect(b.result.current.isStreaming).toBe(false);
    await act(async () => { void b.result.current.send("B 的问题"); });
    await flush();
    expect(streams).toHaveLength(2);
    expect(runningTaskChatKeys()).toBe("task-a\ntask-b");
  });

  it("没发过消息时, 库里的历史照常载入", () => {
    const h = renderHook(() => useTaskChat(opts("task-c")));
    act(() => h.result.current.loadHistory([{ id: "1", role: "user", content: "旧的", ts: "", status: "done" }]));
    expect(h.result.current.messages).toHaveLength(1);
  });

  it("停止 / 清空 才会真的停", async () => {
    const h = renderHook(() => useTaskChat(opts("task-a")));
    await act(async () => { void h.result.current.send("问题"); });
    await flush();
    act(() => h.result.current.cancel());
    expect(streams[0].args.signal.aborted).toBe(true);
    expect(h.result.current.isStreaming).toBe(false);

    await flush();
    await act(async () => { void h.result.current.send("再问"); });
    await flush();
    act(() => h.result.current.reset());
    expect(streams[1].args.signal.aborted).toBe(true);
    expect(h.result.current.messages).toEqual([]);
    act(() => h.result.current.loadHistory([]));
    expect(isTaskChatRunning("task-a")).toBe(false);
  });
});
