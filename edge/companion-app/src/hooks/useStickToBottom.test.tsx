// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useStickToBottom } from "./useStickToBottom";

// jsdom 没有布局: 手动给 scrollHeight / clientHeight, 手动触发 ResizeObserver
let roCallbacks: Array<() => void> = [];
class FakeRO {
  constructor(private cb: () => void) {
    roCallbacks.push(cb);
  }
  observe() {}
  disconnect() {
    roCallbacks = roCallbacks.filter((c) => c !== this.cb);
  }
}

let height = 1000;
function Thread({ sid, msgs }: { sid: string | null; msgs: { role: string }[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const onScroll = useStickToBottom(ref, sid, msgs);
  return (
    <div ref={ref} data-testid="t" onScroll={onScroll}>
      <div />
    </div>
  );
}
function el() {
  return document.querySelector('[data-testid="t"]') as HTMLDivElement;
}
function setup(node: HTMLDivElement) {
  Object.defineProperty(node, "scrollHeight", { configurable: true, get: () => height });
  Object.defineProperty(node, "clientHeight", { configurable: true, get: () => 300 });
}
const grow = (h: number) => {
  height = h;
  act(() => roCallbacks.forEach((c) => c()));
};

beforeEach(() => {
  height = 1000;
  roCallbacks = [];
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver = FakeRO;
});
afterEach(() => {
  cleanup();
  delete (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
});

describe("useStickToBottom", () => {
  it("内容渲染后才长高也跟到底", () => {
    const { rerender } = render(<Thread sid="a" msgs={[]} />);
    setup(el());
    rerender(<Thread sid="a" msgs={[{ role: "assistant" }]} />);
    expect(el().scrollTop).toBe(1000);
    grow(1800); // markdown / 工具块后来才撑开
    expect(el().scrollTop).toBe(1800);
  });

  it("员工往上翻了就不拉回, 发消息又贴底", () => {
    const msgs = [{ role: "assistant" }];
    const { rerender } = render(<Thread sid="a" msgs={[]} />);
    setup(el());
    rerender(<Thread sid="a" msgs={msgs} />);
    el().scrollTop = 100;
    act(() => el().dispatchEvent(new Event("scroll")));
    grow(1500);
    expect(el().scrollTop).toBe(100);
    rerender(<Thread sid="a" msgs={[...msgs, { role: "user" }]} />);
    expect(el().scrollTop).toBe(1500);
  });

  it("切会话强制到底, 不管上个会话停在哪", () => {
    const { rerender } = render(<Thread sid="a" msgs={[]} />);
    setup(el());
    rerender(<Thread sid="a" msgs={[{ role: "assistant" }]} />);
    el().scrollTop = 0;
    act(() => el().dispatchEvent(new Event("scroll")));
    height = 2400;
    rerender(<Thread sid="b" msgs={[{ role: "assistant" }, { role: "assistant" }]} />);
    expect(el().scrollTop).toBe(2400);
  });
});
