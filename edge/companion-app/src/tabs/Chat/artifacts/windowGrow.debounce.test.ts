import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const setSize = vi.fn(async () => {});
const state = { w: 1100 };
vi.mock("@tauri-apps/api/window", () => {
  class PhysicalSize { constructor(public width: number, public height: number) {} }
  class PhysicalPosition { constructor(public x: number, public y: number) {} }
  const win = {
    isFullscreen: async () => false, isMaximized: async () => false,
    outerPosition: async () => ({ x: 100, y: 50 }),
    outerSize: async () => ({ width: state.w, height: 700 }),
    innerSize: async () => ({ width: state.w, height: 700 }),
    setPosition: async () => {},
    setSize: async (s: { width: number }) => { state.w = s.width; await setSize(s as never); },
  };
  return {
    getCurrentWindow: () => win, PhysicalSize, PhysicalPosition,
    currentMonitor: async () => ({ scaleFactor: 1, position: { x: 0, y: 0 }, size: { width: 3000, height: 1000 }, workArea: { position: { x: 0, y: 0 }, size: { width: 3000, height: 1000 } } }),
  };
});

import { _resetGrown, growWindowFor, shrinkWindowBack } from "./windowGrow";

const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); await vi.dynamicImportSettled?.(); for (let i = 0; i < 20; i++) await Promise.resolve(); };

beforeEach(() => {
  vi.useFakeTimers();
  (globalThis as { window?: unknown }).window = { __TAURI_INTERNALS__: {} };
  state.w = 1100; setSize.mockClear(); _resetGrown();
});
afterEach(() => { vi.useRealTimers(); delete (globalThis as { window?: unknown }).window; });

describe("右栏 关 → 马上又开 (11/24 点预览左右晃)", () => {
  it("窗口只长一次, 中间不缩回去", async () => {
    void growWindowFor(360);
    await flush();
    expect(state.w).toBe(1460);
    void shrinkWindowBack();
    void growWindowFor(360); // 开发版 effect 跑两遍 / 切右栏内容
    await vi.advanceTimersByTimeAsync(500);
    await flush();
    expect(setSize).toHaveBeenCalledTimes(1);
    expect(state.w).toBe(1460);
  });

  it("真关掉: 稍等一下缩回原宽", async () => {
    void growWindowFor(360);
    await flush();
    void shrinkWindowBack();
    await vi.advanceTimersByTimeAsync(500);
    await flush();
    expect(state.w).toBe(1100);
  });
});
