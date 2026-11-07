// @vitest-environment jsdom
/** 11/07: 成果区在回答进行中也自动打开; 用户这一轮自己动过右栏就不抢 */
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage, ToolCall } from "../../../types/chat";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a), convertFileSrc: (p: string) => p }));

import { useArtifactAutoOpen } from "./useArtifactAutoOpen";
import { useArtifactStore } from "../../../store/artifacts";

const ROUND = Date.parse("2026-11-07T10:00:00Z");
const user: ChatMessage = { id: "u", role: "user", content: "做个看板", ts: "2026-11-07T10:00:00Z" };
const call = (id: string, status: ToolCall["status"], result?: string): ToolCall => ({ id, name: "write_file", args: {}, status, result });
const asst = (content: string, calls: ToolCall[]): ChatMessage => ({ id: "a", role: "assistant", content, ts: "2026-11-07T10:00:05Z", tool_calls: calls });

beforeEach(() => {
  invoke.mockReset();
  invoke.mockImplementation(async (_c: string, a: { path: string }) => ({
    path: a.path, name: "x", ext: "html", kind: "html", size: 1, mtime_ms: a.path.includes("old") ? ROUND - 60_000 : ROUND + 5_000,
  }));
  useArtifactStore.setState({ path: null, view: null, userActedAt: 0 });
});

describe("useArtifactAutoOpen", () => {
  it("回答进行中: 一步写完新文件就打开, 下一步再写新的就跟过去", async () => {
    const { rerender } = renderHook(({ m, s }) => useArtifactAutoOpen(m, s, "sid"), {
      initialProps: { m: [user, asst("", [call("1", "running")])], s: true },
    });
    expect(useArtifactStore.getState().path).toBeNull();
    rerender({ m: [user, asst("", [call("1", "done", '{"path":"/Users/x/out/a.html"}')])], s: true });
    await waitFor(() => expect(useArtifactStore.getState().path).toBe("/Users/x/out/a.html"));
    rerender({ m: [user, asst("", [call("1", "done", '{"path":"/Users/x/out/a.html"}'), call("2", "done", "写好了 /Users/x/out/b.md")])], s: true });
    await waitFor(() => expect(useArtifactStore.getState().path).toBe("/Users/x/out/b.md"));
  });

  it("进行中不看正文 (路径可能只写了一半); 只是读了的老文件不弹", async () => {
    const { rerender } = renderHook(({ m, s }) => useArtifactAutoOpen(m, s, "sid"), {
      initialProps: { m: [user, asst("看板在 /Users/x/out/rep.htm", [])], s: true },
    });
    rerender({ m: [user, asst("看板在 /Users/x/out/rep.htm", [call("1", "done", "读了 /Users/x/old.pdf")])], s: true });
    await new Promise((r) => setTimeout(r, 20));
    expect(useArtifactStore.getState().path).toBeNull();
    expect(invoke).toHaveBeenCalledTimes(1); // 只查了工具结果里的那个
  });

  it("这一轮用户自己开过别的文件 → 不再自动切走; 结束时也不抢", async () => {
    const { rerender } = renderHook(({ m, s }) => useArtifactAutoOpen(m, s, "sid"), {
      initialProps: { m: [user], s: true },
    });
    useArtifactStore.setState({ path: "/Users/x/mine.pdf", userActedAt: ROUND + 1000 });
    rerender({ m: [user, asst("", [call("1", "done", "/Users/x/out/a.html")])], s: true });
    rerender({ m: [user, asst("做好了 /Users/x/out/a.html", [call("1", "done", "/Users/x/out/a.html")])], s: false });
    await new Promise((r) => setTimeout(r, 20));
    expect(useArtifactStore.getState().path).toBe("/Users/x/mine.pdf");
  });

  it("这一轮用户关了右栏 → 不再自己弹出来", async () => {
    const { rerender } = renderHook(({ m, s }) => useArtifactAutoOpen(m, s, "sid"), { initialProps: { m: [user], s: true } });
    useArtifactStore.setState({ path: null, userActedAt: ROUND + 1000 });
    rerender({ m: [user, asst("", [call("1", "done", "/Users/x/out/a.html")])], s: true });
    await new Promise((r) => setTimeout(r, 20));
    expect(useArtifactStore.getState().path).toBeNull();
  });

  it("一轮结束: 正文里最后提到的新文件", async () => {
    const { rerender } = renderHook(({ m, s }) => useArtifactAutoOpen(m, s, "sid"), { initialProps: { m: [user], s: true } });
    rerender({ m: [user, asst("报告: /Users/x/out/r.docx", [])], s: false });
    await waitFor(() => expect(useArtifactStore.getState().path).toBe("/Users/x/out/r.docx"));
  });

  it("换对话收起右栏, 不算用户操作", () => {
    const { rerender } = renderHook(({ id }) => useArtifactAutoOpen([], false, id), { initialProps: { id: "a" as string | null } });
    useArtifactStore.setState({ path: "/Users/x/a.html" });
    rerender({ id: "b" });
    expect(useArtifactStore.getState()).toMatchObject({ path: null, view: null, userActedAt: 0 });
  });

  it("回答进行中小鲶用上浏览器工具 → 切到浏览器页; 之后写出新文件再切回成果", async () => {
    const browse: ToolCall = { id: "b", name: "mcp__catfish_tools__catfish_browser_goto", args: {}, status: "running" };
    const { rerender } = renderHook(({ m, s }) => useArtifactAutoOpen(m, s, "sid"), { initialProps: { m: [user], s: true } });
    rerender({ m: [user, asst("", [browse])], s: true });
    expect(useArtifactStore.getState().view).toBe("browser");
    rerender({ m: [user, asst("", [{ ...browse, status: "done" }, call("2", "done", "/Users/x/out/a.html")])], s: true });
    await waitFor(() => expect(useArtifactStore.getState()).toMatchObject({ view: "file", path: "/Users/x/out/a.html" }));
  });

  it("这一轮用户关过右栏, 用浏览器也不弹", () => {
    const { rerender } = renderHook(({ m, s }) => useArtifactAutoOpen(m, s, "sid"), { initialProps: { m: [user], s: true } });
    useArtifactStore.setState({ userActedAt: ROUND + 1000 });
    rerender({ m: [user, asst("", [{ id: "b", name: "browser_navigate", args: {}, status: "running" }])], s: true });
    expect(useArtifactStore.getState().view).toBeNull();
  });
});
