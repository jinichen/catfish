// @vitest-environment jsdom
/** 11/15: Markdown 里的 ```a2ui 代码块画成卡片 (按需加载); 别的代码块照旧是代码。 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Markdown } from "./markdown";

const CARD = JSON.stringify([
  { version: "1.0", createSurface: { surfaceId: "m", catalogId: "catfish:cards/v1" } },
  { version: "1.0", updateComponents: { surfaceId: "m", components: [{ id: "root", component: "Text", text: "卡片正文" }] } },
]);

afterEach(cleanup);

describe("Markdown · A2UI 卡片", () => {
  it("```a2ui 画成卡片, 不留一坨 JSON; 不在对话页 (没有 Provider) 时说去对话里填", { timeout: 20_000 }, async () => {
    const { container } = render(<Markdown text={"出一张卡：\n\n```a2ui\n" + CARD + "\n```\n"} />);
    // 卡片组件按需加载, 第一次要转译 A2UI + Lit: 整套测试一起跑时 1 秒 (waitFor 默认) 不够
    await waitFor(() => expect(container.querySelector(".a2ui-card")).toBeTruthy(), { timeout: 15_000 });
    expect(container.querySelector("pre")).toBeNull();
    expect(container.textContent).not.toContain("createSurface");
    expect(screen.getByText(/在「对话」里打开才能填写/)).toBeTruthy();
  });

  it("别的代码块不受影响", () => {
    const { container } = render(<Markdown text={"```json\n{\"a\":1}\n```"} />);
    expect(container.querySelector("pre")).toBeTruthy();
    expect(container.querySelector(".a2ui-card")).toBeNull();
  });
});
