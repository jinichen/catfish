// @vitest-environment jsdom
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "./markdown";

describe("Markdown 流式重渲染", () => {
  it("新来的字只原地更新, 已有段落 / 列表不拆掉重建 (11/24 小鲶回复时上下跳)", () => {
    const { container, rerender } = render(<Markdown text={"第一段\n\n- 一\n- 二"} />);
    const p = container.querySelector("p");
    const li = container.querySelector("li");
    rerender(<Markdown text={"第一段\n\n- 一\n- 二\n- 三\n\n后面又来一段"} />);
    expect(container.querySelector("p")).toBe(p);
    expect(container.querySelector("li")).toBe(li);
  });
});
