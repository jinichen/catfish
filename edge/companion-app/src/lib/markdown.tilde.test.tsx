// @vitest-environment jsdom
/** 11/11: 单个 `~` 不算删除线 (鸿波截图:「上周（11-02~11-08）…各举1~2 条」两个 ~ 配成一对, 中间整段被划掉); `~~…~~` 照旧是删除线。 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Markdown } from "./markdown";

afterEach(cleanup);

describe("Markdown 删除线", () => {
  it("日期范围 / 文件名里的单个 ~ 原样显示, 不划线", () => {
    // 原话: 改之前 remark-gfm 默认把「02~11 … 1~2」之间整段包进 <del>
    const { container } = render(<Markdown text={"只回一句「上周（11-02~11-08）没有采集记录」，不出报告；有数据才出四块（选题类型+开头写法各举1~2 条原视频）"} />);
    expect(container.querySelector("del")).toBeNull();
    expect(container.textContent).toContain("11-02~11-08");
    expect(container.textContent).toContain("各举1~2 条");
  });
  it("两个 ~~ 才是删除线", () => {
    const { container } = render(<Markdown text={"这句 ~~作废~~ 了"} />);
    expect(container.querySelector("del")?.textContent).toBe("作废");
  });
});
