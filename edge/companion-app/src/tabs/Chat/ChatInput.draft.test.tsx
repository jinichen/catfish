// @vitest-environment jsdom
/** 11/24 「工作台输入还没发送, 切到别的页再切回来输入被清空了」: 没发的字按会话存在组件外面, 卸了再装还在; 换会话各是各的; 发出去就清掉。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(async () => null) }));

import ChatInput from "./ChatInput";
import { useChatStore } from "../../store/chat";
import { _clearDraftsForTest } from "../../store/composerDraft";

const props = { isStreaming: false, onSend: vi.fn(), onCancel: vi.fn(), onEnqueue: vi.fn(), onReset: vi.fn() };
const box = () => screen.getByRole("textbox") as HTMLTextAreaElement;

beforeEach(() => {
  _clearDraftsForTest();
  props.onSend.mockReset();
  useChatStore.setState({ persistedSessionId: "s1" });
});
afterEach(cleanup);

describe("对话输入框草稿", () => {
  it("切到别的页 (组件卸掉) 再回来, 没发的字还在", () => {
    const first = render(<ChatInput {...props} />);
    fireEvent.change(box(), { target: { value: "帮我查一下明天的会" } });
    first.unmount();
    render(<ChatInput {...props} />);
    expect(box().value).toBe("帮我查一下明天的会");
  });

  it("换会话: 各是各的草稿; 切回来原来那份还在", () => {
    render(<ChatInput {...props} />);
    fireEvent.change(box(), { target: { value: "会话一没发的" } });
    act(() => useChatStore.setState({ persistedSessionId: "s2" }));
    expect(box().value).toBe("");
    fireEvent.change(box(), { target: { value: "会话二没发的" } });
    act(() => useChatStore.setState({ persistedSessionId: "s1" }));
    expect(box().value).toBe("会话一没发的");
  });

  it("发出去就清掉, 再回来是空的", () => {
    const first = render(<ChatInput {...props} />);
    fireEvent.change(box(), { target: { value: "发了" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(props.onSend).toHaveBeenCalledWith("发了", []);
    first.unmount();
    render(<ChatInput {...props} />);
    expect(box().value).toBe("");
  });
});
