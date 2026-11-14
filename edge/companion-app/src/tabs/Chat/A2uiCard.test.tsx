// @vitest-environment jsdom
/** 对话里的 A2UI 卡片 (11/15 试点): 坏的不画、缺组件不画、流式中不报错、画得出来的能交、换了挂载点填的不丢。 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import A2uiCard, { _resetCardCacheForTest } from "./A2uiCard";

const create = { version: "1.0", createSurface: { surfaceId: "t", catalogId: "catfish:cards/v1" } };
const comps = (components: unknown[]) => ({ version: "1.0", updateComponents: { surfaceId: "t", components } });
const CARD = JSON.stringify([
  create,
  comps([
    { id: "root", component: "Column", children: ["title", "name", "go"] },
    { id: "title", component: "Text", text: "**测试卡** · 副标题" },
    { id: "name", component: "TextField", label: "名字", value: { "@path": "/r/名字" } },
    { id: "go", component: "Button", child: "go_l", action: { event: { name: "t_submit", context: { record: { "@path": "/r" } } } } },
    { id: "go_l", component: "Text", text: "交" },
  ]),
  { version: "1.0", updateDataModel: { surfaceId: "t", path: "/r", value: { 名字: "小王" } } },
]);

afterEach(() => { cleanup(); _resetCardCacheForTest(); });

describe("A2uiCard", () => {
  it("JSON 坏了: 说格式不对, 原文能看; 流式输出中只说生成中", () => {
    render(<A2uiCard source='[{"version":"1.0"' interactive streaming={false} submitted={false} />);
    expect(screen.getByRole("note").textContent).toContain("这张卡片的格式不对");
    cleanup();
    render(<A2uiCard source='[{"version":"1.0"' interactive streaming submitted={false} />);
    expect(screen.getByText("卡片生成中…")).toBeTruthy();
  });

  it("引用了没写的组件: 不画残缺的卡, 说缺了哪块", () => {
    const src = JSON.stringify([create, comps([{ id: "root", component: "Column", children: ["a", "row3"] }, { id: "a", component: "Text", text: "x" }])]);
    render(<A2uiCard source={src} interactive streaming={false} submitted={false} />);
    expect(screen.getByRole("note").textContent).toContain("卡片缺了一部分 (row3)");
  });

  it("白名单外的组件 (Image 会去外网拉图) 不画", () => {
    const src = JSON.stringify([create, comps([{ id: "root", component: "Image", url: "https://tracker.example.com/p.gif" }])]);
    render(<A2uiCard source={src} interactive streaming={false} submitted={false} />);
    expect(screen.getByRole("note").textContent).toContain("不允许的组件 (Image)");
  });

  it("画得出来; 交过的变灰并说已交; 只读的地方说去对话里填", () => {
    const { container } = render(<A2uiCard source={CARD} interactive streaming={false} submitted />);
    expect(container.querySelector(".a2ui-card")).toBeTruthy();
    expect(screen.getByText("已交给小鲶 ✓")).toBeTruthy();
    cleanup();
    render(<A2uiCard source={CARD} interactive={false} streaming={false} submitted={false} />);
    expect(screen.getByText(/在「对话」里打开才能填写/)).toBeTruthy();
  });

  it("按钮的 action → 拼成提交那句话交出去, 只交一次; 同一张卡重新挂载用的是同一份数据", async () => {
    const onSubmit = vi.fn();
    const { unmount } = render(<A2uiCard source={CARD} interactive streaming={false} submitted={false} onSubmit={onSubmit} />);
    const { buildCardForTest } = await import("./A2uiCard");
    const built = buildCardForTest(CARD);
    if (!("processor" in built)) throw new Error("没建出来");
    const payload = { name: "t_submit", surfaceId: "t", sourceComponentId: "go", timestamp: "", context: { record: { 名字: "小王" } } };
    act(() => { built.handlers.onAction?.(payload); });
    act(() => { built.handlers.onAction?.(payload); });
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0]).toContain("（填好了卡片「测试卡」）");
    expect(onSubmit.mock.calls[0][0]).toContain('"名字": "小王"');
    unmount();
    expect(buildCardForTest(CARD)).toBe(built);
  });
});
