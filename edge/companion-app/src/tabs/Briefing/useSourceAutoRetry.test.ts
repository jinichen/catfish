// @vitest-environment jsdom
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SOURCE_RETRY_DELAY_MS, SOURCE_RETRY_MAX, useSourceAutoRetry } from "./useSourceAutoRetry";

const ok = { ok: true };
const bad = { ok: false };

describe("useSourceAutoRetry", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("数据源失败导致没素材时, 20 秒后重拉, 最多 3 次", () => {
    const reload = vi.fn();
    const failed = { emails: ok, events: bad, todos: bad };
    const { rerender } = renderHook(
      ({ statuses }) => useSourceAutoRetry("no_data", statuses, 0, reload),
      { initialProps: { statuses: failed } },
    );
    for (let i = 1; i <= SOURCE_RETRY_MAX + 2; i += 1) {
      vi.advanceTimersByTime(SOURCE_RETRY_DELAY_MS);
      // 每次重拉后页面会带着新的诊断结果重新渲染
      rerender({ statuses: { ...failed } });
    }
    expect(reload).toHaveBeenCalledTimes(SOURCE_RETRY_MAX);
  });

  it("三个来源都正常、只是没数据时不重拉", () => {
    const reload = vi.fn();
    renderHook(() => useSourceAutoRetry("no_data", { emails: ok, events: ok, todos: ok }, 0, reload));
    vi.advanceTimersByTime(SOURCE_RETRY_DELAY_MS * 5);
    expect(reload).not.toHaveBeenCalled();
  });
});
