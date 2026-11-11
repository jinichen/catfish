// @vitest-environment jsdom
/** 11/11 鸿波截图: 对话里存了草稿跳到邮件页, 列表切到了草稿箱, 右边却是收件箱第一封 (还挂着「发送这封草稿」)。
 *
 * 跳转那一步消费 store.pendingDraftFocus (zustand set) —— 它让 React 先**同步**重画一次, 那一帧里
 * 「草稿已选中」「换到草稿箱」这些 setState 还没生效: 没选中、没有待跳的草稿、列表还是缓存的收件箱,
 * 自动打开就选了收件箱第一封; 它的 setState 排在选草稿后面, 把草稿盖掉了。
 * 这里用真的 zustand store 和真的 useDraftFocus 走一遍。
 */
import { act, cleanup, render } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { create } from "zustand";

const store = vi.hoisted(() => ({ ref: null as null | ReturnType<typeof makeStore> }));
function makeStore() {
  return create<{ pendingDraftFocus: string | null; consumeDraftFocus: () => string | null }>((set, get) => ({
    pendingDraftFocus: null,
    consumeDraftFocus: () => {
      const id = get().pendingDraftFocus;
      if (id) set({ pendingDraftFocus: null });
      return id;
    },
  }));
}
vi.mock("../../store/ui", () => {
  store.ref = makeStore();
  return { useUIStore: store.ref };
});

import type { MailFolder } from "./components/FolderTabs";
import { useDraftFocus } from "./useDraftFocus";
import { useAutoOpenFirst, useMailSelection } from "./useMailSelection";

const INBOX = [{ id: "h1" }, { id: "h2" }];

let seen: { selectedId: string | null; folder: MailFolder } = { selectedId: null, folder: "Inbox" };
function Page() {
  // 邮件页挂载时先铺内存里缓存的收件箱 (EmailTab 的 items 初值)
  const [items, setItems] = useState<{ id: string }[]>(INBOX);
  const [folder, setFolder] = useState<MailFolder>("Inbox");
  const sel = useMailSelection();
  useDraftFocus({ folder, setFolder, clearItems: () => setItems([]), setSelectedId: sel.setSelectedId, reload: () => {} });
  useAutoOpenFirst(sel, items, false);
  seen = { selectedId: sel.selectedId, folder };
  return null;
}

describe("从对话跳到草稿", () => {
  afterEach(() => cleanup());

  it("打开的是那封草稿, 不被自动打开的收件箱第一封盖掉", async () => {
    store.ref!.setState({ pendingDraftFocus: "draft-9" });
    await act(async () => { render(<Page />); });
    expect(seen).toEqual({ selectedId: "draft-9", folder: "Drafts" });
  });

  it("没有待跳的草稿时照常打开第一封", async () => {
    store.ref!.setState({ pendingDraftFocus: null });
    await act(async () => { render(<Page />); });
    expect(seen.selectedId).toBe("h1");
  });
});
