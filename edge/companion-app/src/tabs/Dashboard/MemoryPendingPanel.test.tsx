// @vitest-environment jsdom
/** 11/4: 待你确认的记忆整理建议 —— USER / MEMORY 都列, 点同意 / 不要才改, 过期的只能放弃。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MemoryPendingItem } from "../../lib/tauri";

const api = vi.hoisted(() => ({
  items: [] as MemoryPendingItem[],
  resolve: vi.fn(async (_id: string, _approve: boolean) => ({ ok: true, applied: true })),
}));
vi.mock("../../lib/tauri", () => ({
  hermesMemoryPendingList: vi.fn(async () => ({ ok: true, items: api.items })),
  hermesMemoryPendingResolve: api.resolve,
}));

import MemoryPendingPanel from "./MemoryPendingPanel";

const item = (over: Partial<MemoryPendingItem>): MemoryPendingItem => ({
  id: "aaaa1111", target: "memory", origin: "background_review", created_at: 1_762_000_000, stale: false,
  ops: [{ action: "remove", entry: "旧口径", pinned: true, content: "", old_text: "" }], ...over,
});

async function mount(nearlyFull = false) {
  const onChanged = vi.fn();
  await act(async () => {
    render(<MemoryPendingPanel onChanged={onChanged} nearlyFull={nearlyFull} />);
  });
  return onChanged;
}

afterEach(() => {
  cleanup();
  api.resolve.mockClear();
});

describe("MemoryPendingPanel", () => {
  it("USER 和 MEMORY 的建议都列出来, 说清要删 / 改什么", async () => {
    api.items = [
      item({}),
      item({ id: "bbbb2222", target: "user",
        ops: [{ action: "replace", entry: "我叫鸿波", pinned: true, content: "我叫鸿波, 在福富做资质", old_text: "" }] }),
    ];
    await mount();
    expect(screen.getByText("待你确认的整理建议 (2)")).toBeTruthy();
    expect(screen.getByText(/USER · 员工本人/)).toBeTruthy();
    expect(screen.getByText(/MEMORY · 项目/)).toBeTruthy();
    expect(screen.getByText("旧口径")).toBeTruthy();
    expect(screen.getByText(/改成: 我叫鸿波, 在福富做资质/)).toBeTruthy();
  });

  it("patch 显示换掉哪一段 + 所在那条", async () => {
    api.items = [item({ ops: [{ action: "patch", entry: "研发 2200+ 人", pinned: true, content: "4200+", old_text: "2200+" }] })];
    await mount();
    expect(screen.getByText("2200+")).toBeTruthy();
    expect(screen.getByText(/所在那条: 研发 2200\+ 人/)).toBeTruthy();
  });

  it("点同意才批, 批完通知卡片刷新", async () => {
    api.items = [item({})];
    const onChanged = await mount();
    await act(async () => {
      fireEvent.click(screen.getByText("同意"));
    });
    expect(api.resolve).toHaveBeenCalledWith("aaaa1111", true);
    expect(onChanged).toHaveBeenCalled();
  });

  it("全部同意只批还能用的, 过期的不批", async () => {
    api.items = [item({}), item({ id: "cccc3333" }), item({ id: "dddd4444", stale: true })];
    await mount();
    expect(screen.getByText(/用不上了/)).toBeTruthy();
    await act(async () => {
      fireEvent.click(screen.getByText("全部同意 (2)"));
    });
    expect(api.resolve.mock.calls).toEqual([["aaaa1111", true], ["cccc3333", true]]);
  });

  it("没有建议但记忆快满了: 提示去整理", async () => {
    api.items = [];
    await mount(true);
    expect(screen.getByText(/记忆快满了/)).toBeTruthy();
  });
});
