// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ read: vi.fn(), mark: vi.fn() }));
vi.mock("../../lib/tauri", () => ({ emailReadMessage: mocks.read, emailMarkRead: mocks.mark }));

import { MAIL_GONE_TEXT, isMailGoneError, useEmailDetail } from "./useEmailDetail";
import { AUTO_READ_DWELL_MS } from "./useMailSelection";

// CLI 对 DataNotFoundError 打的 stderr 原文 (cli_read._cmd_read), 两种来源各一条
const APPLE_GONE =
  "catfish-email: 邮件不存在 [apple_mail]: Mail 里找不到这条消息 (id 错 / 邮件已删 / 不在该账号下).";
const IMAP_GONE = "catfish-email: 邮件不存在 [imap]: 邮件不存在或已删除: uid=8526";

describe("useEmailDetail", () => {
  beforeEach(() => { vi.clearAllMocks(); });
  afterEach(() => { cleanup(); });

  it("recognises the CLI's not-found message from every source, and nothing else", () => {
    expect(isMailGoneError(APPLE_GONE)).toBe(true);
    expect(isMailGoneError(IMAP_GONE)).toBe(true);
    expect(isMailGoneError("catfish-email: [apple_mail] 读邮件失败: AppleScript 超时 (60s)")).toBe(false);
  });

  it("a mail that is gone is dropped from the list with a plain explanation", async () => {
    mocks.read.mockRejectedValue(new Error(APPLE_GONE));
    const onGone = vi.fn();
    const onRead = vi.fn();
    const { result } = renderHook(() =>
      useEmailDetail({ selectedId: "apple_mail|Google|2750", onRead, onGone }));
    await act(async () => {});
    expect(onGone).toHaveBeenCalledWith("apple_mail|Google|2750");
    expect(result.current.detailError).toBe(MAIL_GONE_TEXT);
    expect(result.current.detailLoading).toBe(false);
  });

  it("other failures keep the original error text and do not touch the list", async () => {
    mocks.read.mockRejectedValue(new Error("AppleScript 超时"));
    const onGone = vi.fn();
    const { result } = renderHook(() =>
      useEmailDetail({ selectedId: "apple_mail|Google|1", onRead: vi.fn(), onGone }));
    await act(async () => {});
    expect(onGone).not.toHaveBeenCalled();
    expect(result.current.detailError).toBe("AppleScript 超时");
  });

  it("a read that finishes after switching to another mail does not overwrite it", async () => {
    let finishFirst!: (v: string) => void;
    mocks.read
      .mockImplementationOnce(() => new Promise((done) => { finishFirst = done; }))
      .mockResolvedValueOnce(JSON.stringify({ id: "b", subject: "B", is_read: true }));
    const onRead = vi.fn();
    const { result, rerender } = renderHook(
      ({ id }) => useEmailDetail({ selectedId: id, onRead, onGone: vi.fn() }),
      { initialProps: { id: "a" } },
    );
    rerender({ id: "b" });
    await act(async () => {});
    await act(async () => { finishFirst(JSON.stringify({ id: "a", subject: "A", is_read: true })); });
    expect(result.current.detail?.subject).toBe("B");
    expect(onRead).toHaveBeenCalledTimes(1);
    expect(onRead).toHaveBeenCalledWith("b");
  });
});

// 11/11 (1.2.147): 自动打开的那封停够 3 秒才标已读; 点了就立刻标; 切走就不标
describe("useEmailDetail · 自动打开的停留标已读", () => {
  beforeEach(() => { vi.clearAllMocks(); vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); cleanup(); });
  const unread = (id: string) => JSON.stringify({ id, subject: id.toUpperCase(), is_read: false });

  it("reads without marking, then marks after the dwell", async () => {
    mocks.read.mockResolvedValue(unread("a"));
    mocks.mark.mockResolvedValue("{}");
    const onRead = vi.fn();
    const { result } = renderHook(() =>
      useEmailDetail({ selectedId: "a", autoRead: true, onRead, onGone: vi.fn() }));
    await act(async () => {});
    expect(mocks.read).toHaveBeenCalledWith("a", { markRead: false });
    await act(async () => { await vi.advanceTimersByTimeAsync(AUTO_READ_DWELL_MS - 100); });
    expect(mocks.mark).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(200); });
    expect(mocks.mark).toHaveBeenCalledWith("a", true);
    expect(onRead).toHaveBeenCalledWith("a");
    expect(result.current.detail?.is_read).toBe(true);
  });

  it("moving on within the dwell leaves it unread", async () => {
    mocks.read.mockImplementation(async (id: string) => unread(id));
    const { rerender, unmount } = renderHook(
      ({ id }) => useEmailDetail({ selectedId: id, autoRead: true, onRead: vi.fn(), onGone: vi.fn() }),
      { initialProps: { id: "a" } });
    await act(async () => {});
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    rerender({ id: "b" });
    await act(async () => {});
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    unmount(); // 离开邮件页
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(mocks.mark).not.toHaveBeenCalled();
  });

  it("clicking the auto-opened mail marks it at once, without reading it again", async () => {
    mocks.read.mockResolvedValue(unread("a"));
    mocks.mark.mockResolvedValue("{}");
    const { rerender } = renderHook(
      ({ auto }) => useEmailDetail({ selectedId: "a", autoRead: auto, onRead: vi.fn(), onGone: vi.fn() }),
      { initialProps: { auto: true } });
    await act(async () => {});
    rerender({ auto: false });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(mocks.mark).toHaveBeenCalledWith("a", true);
    expect(mocks.read).toHaveBeenCalledTimes(1);
  });

  it("a manually opened mail is marked by the read itself, not a second time", async () => {
    mocks.read.mockResolvedValue(JSON.stringify({ id: "a", subject: "A", is_read: true }));
    renderHook(() => useEmailDetail({ selectedId: "a", onRead: vi.fn(), onGone: vi.fn() }));
    await act(async () => {});
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(mocks.read).toHaveBeenCalledWith("a", undefined);
    expect(mocks.mark).not.toHaveBeenCalled();
  });
});
