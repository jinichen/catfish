// @vitest-environment jsdom
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useStickToBottom } from "./useStickToBottom";

// jsdom 没有布局: 三条消息的高度自己管, getBoundingClientRect 按 scrollTop 算
let roCallbacks: Array<() => void> = [];
class FakeRO {
  constructor(private cb: () => void) { roCallbacks.push(cb); }
  observe() {}
  disconnect() { roCallbacks = roCallbacks.filter((c) => c !== this.cb); }
}
let heights = [400, 900, 400];
const rect = (top: number, h: number) => ({ top, bottom: top + h, height: h, left: 0, right: 0, width: 0, x: 0, y: top, toJSON() {} }) as DOMRect;

function Thread() {
  const ref = useRef<HTMLDivElement>(null);
  const onScroll = useStickToBottom(ref, "s", [{ role: "assistant" }], false);
  return (
    <div ref={ref} data-testid="t" onScroll={onScroll}>
      <div>{heights.map((_, i) => <div key={i} data-i={i} />)}</div>
    </div>
  );
}

beforeEach(() => {
  heights = [400, 900, 400];
  roCallbacks = [];
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver = FakeRO;
});
afterEach(() => {
  cleanup();
  delete (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
});

describe("useStickToBottom 往上翻着看时尺寸变了", () => {
  it("右栏打开文字重新折行, 正在看的那段留在原处", () => {
    render(<Thread />);
    const t = document.querySelector('[data-testid="t"]') as HTMLDivElement;
    Object.defineProperty(t, "scrollHeight", { configurable: true, get: () => heights.reduce((a, b) => a + b, 0) });
    Object.defineProperty(t, "clientHeight", { configurable: true, get: () => 300 });
    t.getBoundingClientRect = () => rect(0, 300);
    t.querySelectorAll("[data-i]").forEach((n) => {
      const i = Number((n as HTMLElement).dataset.i);
      (n as HTMLElement).getBoundingClientRect = () =>
        rect(heights.slice(0, i).reduce((a, b) => a + b, 0) - t.scrollTop, heights[i]);
    });

    // 先在底部, 员工用触控板往上翻到第二条的一半 (400 + 450)
    t.scrollTop = 1400;
    fireEvent.scroll(t);
    fireEvent.wheel(t);
    t.scrollTop = 850;
    fireEvent.scroll(t);

    // 右栏打开, 对话区变窄: 第一条从 400 折成 600 高, 第二条从 900 折成 1300
    heights = [600, 1300, 600];
    act(() => roCallbacks.forEach((c) => c()));
    // 还是看在第二条的一半: 600 + 650
    expect(t.scrollTop).toBe(1250);
  });
});
