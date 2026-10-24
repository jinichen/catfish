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
function Thread({ sid, msgs, busy = false }: { sid: string | null; msgs: { role: string }[]; busy?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const onScroll = useStickToBottom(ref, sid, msgs, busy);
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

  it("滚到底的滚动事件还没到, 内容先长了一大截 —— 不算员工往上翻, 继续跟 (10/24 跑指令时不贴底)", () => {
    const { rerender } = render(<Thread sid="a" msgs={[]} />);
    setup(el());
    rerender(<Thread sid="a" msgs={[{ role: "assistant" }]} />);
    expect(el().scrollTop).toBe(1000);
    height = 1600; // 工具结果块一下冒出来, 600px
    act(() => el().dispatchEvent(new Event("scroll"))); // 上一次滚到底的事件这时才到
    grow(1700);
    expect(el().scrollTop).toBe(1700);
  });

  it("跑指令期间翻上去也拉回末尾; 跑完再贴一次底 (鸿波: 不要按钮, 直接到末尾)", () => {
    const msgs = [{ role: "assistant" }];
    const { rerender } = render(<Thread sid="a" msgs={[]} />);
    setup(el());
    rerender(<Thread sid="a" msgs={msgs} busy />);
    el().scrollTop = 100;
    act(() => el().dispatchEvent(new Event("scroll")));
    grow(1500);
    expect(el().scrollTop).toBe(1500);
    el().scrollTop = 200;
    act(() => el().dispatchEvent(new Event("scroll")));
    height = 1900;
    rerender(<Thread sid="a" msgs={msgs} busy={false} />);
    expect(el().scrollTop).toBe(1900);
  });

  it("员工没动, scrollTop 自己掉了 (整块重挂被夹) —— 立刻滚回底 (10/24 诊断框实测)", () => {
    const { rerender } = render(<Thread sid="a" msgs={[]} />);
    setup(el());
    rerender(<Thread sid="a" msgs={[{ role: "assistant" }]} />);
    expect(el().scrollTop).toBe(1000);
    el().scrollTop = 482; // 1000 → 482, 没有滚轮 / 键盘
    act(() => el().dispatchEvent(new Event("scroll")));
    expect(el().scrollTop).toBe(1000);
    grow(1200); // 之后内容照样跟
    expect(el().scrollTop).toBe(1200);
  });

  it("员工往上翻了就不拉回, 发消息又贴底", () => {
    const msgs = [{ role: "assistant" }];
    const { rerender } = render(<Thread sid="a" msgs={[]} />);
    setup(el());
    rerender(<Thread sid="a" msgs={msgs} />);
    act(() => el().dispatchEvent(new Event("wheel")));
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
    act(() => el().dispatchEvent(new Event("wheel")));
    el().scrollTop = 0;
    act(() => el().dispatchEvent(new Event("scroll")));
    height = 2400;
    rerender(<Thread sid="b" msgs={[{ role: "assistant" }, { role: "assistant" }]} />);
    expect(el().scrollTop).toBe(2400);
  });
});
