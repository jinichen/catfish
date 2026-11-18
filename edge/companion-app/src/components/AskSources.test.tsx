// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AskSources, sourcesLine } from "./AskSources";

const WEB = { kind: "网页", title: "信息安全服务资质认证实施规则", where: "https://www.isccc.gov.cn/a.pdf" };
const WIKI = { kind: "知识库", title: "金智达", where: "wiki/entities/金智达.md" };

describe("小鲶回答下面的依据 (11/18, 改纪要 / 会中问 / 群聊 @小鲶 共用)", () => {
  it("本机的一行, 不把网页算成「你电脑上的」", () => {
    expect(sourcesLine([WEB, WIKI, { kind: "本机文件", title: "清单.xlsx", where: "/x" }]))
      .toBe("依据 (你电脑上的): 知识库「金智达」 · 文件「清单.xlsx」");
    expect(sourcesLine([WEB])).toBe("");
  });

  it("网页列标题 + 网站, 链接指向原文; 不是 http(s) 的不出链接", () => {
    render(<AskSources sources={[WEB, { kind: "网页", title: "坏的", where: "javascript:alert(1)" }, WIKI]} style={{}} localIcon="🔎 " />);
    expect(screen.getByText("《信息安全服务资质认证实施规则》").getAttribute("href")).toBe("https://www.isccc.gov.cn/a.pdf");
    expect(screen.getByText(/isccc\.gov\.cn/)).toBeTruthy();
    expect(screen.queryByText("《坏的》")).toBeNull();
    expect(screen.getByText("🔎 依据 (你电脑上的): 知识库「金智达」")).toBeTruthy();
  });

  it("什么都没有就什么都不显示", () => {
    const { container } = render(<AskSources sources={[]} style={{}} />);
    expect(container.textContent).toBe("");
  });
});
