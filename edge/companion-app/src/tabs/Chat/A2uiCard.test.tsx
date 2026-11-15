// @vitest-environment jsdom
/** 对话卡片 (A2UI): 官方 SDK 编出来的卡片画得出来; 交过的 / 只读的; 按钮交出上游标准 action; 卸了重装填的不丢。 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import meeting from "./__fixtures__/a2ui_card_meeting.json";
import A2uiCard, { _resetCardCacheForTest, buildCardForTest } from "./A2uiCard";

const ID = "c_20261115_093012_a1b2c3";
afterEach(() => { cleanup(); _resetCardCacheForTest(); });

describe("A2uiCard", () => {
  it("官方 SDK 编出来的卡片 (createSurface 内联组件) 画得出来", () => {
    const { container } = render(<A2uiCard cardId={ID} messages={meeting} interactive submitted={false} />);
    expect(container.querySelector(".a2ui-card")).toBeTruthy();
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("白名单外的组件不画, 说原因", () => {
    const bad = [{ version: "1.0", createSurface: { surfaceId: "main", catalogId: "x", components: [{ id: "root", component: "Image", url: "https://t.example/p.gif" }] } }];
    render(<A2uiCard cardId={ID} messages={bad} interactive submitted={false} />);
    expect(screen.getByRole("note").textContent).toContain("不允许的组件 (Image)");
  });

  it("交过的变灰说已交; 只读的地方说去对话里填", () => {
    render(<A2uiCard cardId={ID} messages={meeting} interactive submitted />);
    expect(screen.getByText("已交给小鲶 ✓")).toBeTruthy();
    cleanup();
    render(<A2uiCard cardId={ID} messages={meeting} interactive={false} submitted={false} />);
    expect(screen.getByText(/在「对话」里打开才能填写/)).toBeTruthy();
  });

  it("按钮 → 上游标准 action 消息拼成的那句话, 只交一次; 同一编号重装用同一份数据", () => {
    const onSubmit = vi.fn();
    const { unmount } = render(<A2uiCard cardId={ID} messages={meeting} interactive submitted={false} onSubmit={onSubmit} />);
    const built = buildCardForTest(ID, meeting);
    if (!("processor" in built)) throw new Error("没建出来");
    const payload = { name: "book_meeting", surfaceId: "main", sourceComponentId: "body_children_3", timestamp: "t", context: { meeting: { time: [] } } };
    act(() => { built.handlers.onAction?.(payload); });
    act(() => { built.handlers.onAction?.(payload); });
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const text = onSubmit.mock.calls[0][0] as string;
    expect(text).toContain("（填好了卡片「约周例会」）");
    expect(text).toContain('"version": "1.0"');
    expect(text).toContain('"name": "book_meeting"');
    expect(text).toContain(`<!-- card:${ID} -->`);
    unmount();
    expect(buildCardForTest(ID, meeting)).toBe(built);
  });
});
