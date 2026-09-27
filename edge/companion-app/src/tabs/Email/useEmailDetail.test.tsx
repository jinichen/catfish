// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ read: vi.fn() }));
vi.mock("../../lib/tauri", () => ({ emailReadMessage: mocks.read }));

import { MAIL_GONE_TEXT, isMailGoneError, useEmailDetail } from "./useEmailDetail";

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
