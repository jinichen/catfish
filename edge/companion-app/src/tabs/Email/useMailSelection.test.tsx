// @vitest-environment jsdom
/** 进邮件页自动开第一封、删掉后接着开下一封 (11/11, 1.2.147) */
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const ui = vi.hoisted(() => ({ pendingDraftFocus: null as string | null }));
vi.mock("../../store/ui", () => ({
  useUIStore: (sel: (s: typeof ui) => unknown) => sel(ui),
}));

import { nextAfterDelete, useAutoOpenFirst, useMailSelection } from "./useMailSelection";

const L = (...ids: string[]) => ids.map((id) => ({ id }));

function setup(list: { id: string }[], hold = false) {
  return renderHook(
    ({ list, hold }) => {
      const sel = useMailSelection();
      const afterDelete = useAutoOpenFirst(sel, list, hold);
      return { sel, afterDelete };
    },
    { initialProps: { list, hold } },
  );
}

describe("nextAfterDelete", () => {
  it("opens the one below, else the one above, else nothing", () => {
    expect(nextAfterDelete(L("a", "b", "c"), "b")).toBe("c");
    expect(nextAfterDelete(L("a", "b", "c"), "c")).toBe("b");
    expect(nextAfterDelete(L("a"), "a")).toBeNull();
    expect(nextAfterDelete(L("a", "b"), "gone")).toBe("a");
  });
});

describe("useAutoOpenFirst", () => {
  afterEach(() => { cleanup(); ui.pendingDraftFocus = null; });

  it("opens the first mail on arrival, as an auto selection", () => {
    const { result } = setup(L("a", "b"));
    expect(result.current.sel.selectedId).toBe("a");
    expect(result.current.sel.autoSelected).toBe(true);
  });

  it("waits for the list when it is still empty", () => {
    const { result, rerender } = setup([]);
    expect(result.current.sel.selectedId).toBeNull();
    rerender({ list: L("x"), hold: false });
    expect(result.current.sel.selectedId).toBe("x");
  });

  it("a click turns it into a manual selection", () => {
    const { result } = setup(L("a", "b"));
    act(() => result.current.sel.setSelectedId("b"));
    expect(result.current.sel.selectedId).toBe("b");
    expect(result.current.sel.autoSelected).toBe(false);
  });

  it("after a delete opens the next mail, then the previous, then nothing", () => {
    const { result, rerender } = setup(L("a", "b", "c"));
    act(() => result.current.sel.setSelectedId("b"));
    // EmailTab 里删除和列表更新在同一批: 一起放进 act
    act(() => { result.current.afterDelete("b"); rerender({ list: L("a", "c"), hold: false }); });
    expect(result.current.sel.selectedId).toBe("c");
    expect(result.current.sel.autoSelected).toBe(true);
    act(() => { result.current.afterDelete("c"); rerender({ list: L("a"), hold: false }); });
    expect(result.current.sel.selectedId).toBe("a");
    act(() => { result.current.afterDelete("a"); rerender({ list: [], hold: false }); });
    expect(result.current.sel.selectedId).toBeNull();
  });

  it("does not open anything while composing a new mail or before a draft jump lands", () => {
    expect(setup(L("a"), true).result.current.sel.selectedId).toBeNull();
    cleanup();
    ui.pendingDraftFocus = "draft-1";
    expect(setup(L("a")).result.current.sel.selectedId).toBeNull();
  });

  it("clearing the selection (folder switch) opens the new list's first mail", () => {
    const { result, rerender } = setup(L("a"));
    act(() => { result.current.sel.setSelectedId(null); rerender({ list: [], hold: false }); });
    expect(result.current.sel.selectedId).toBeNull();
    rerender({ list: L("d1", "d2"), hold: false });
    expect(result.current.sel.selectedId).toBe("d1");
  });
});
