// @vitest-environment jsdom
/** 11/12 部门知识库: 摘要去格式符号 / 重复标题 / 本机路径; 分享人显示; 不收也能预览全文。 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const me = vi.hoisted(() => ({ fetchWithAuth: vi.fn() }));
vi.mock("../../lib/me", () => me);
vi.mock("../../lib/env", () => ({ config: { gatewayUrl: "http://gw" } }));

import { cleanPreview, hideLocalPaths, personLabel, WikiDocPreview } from "./WikiHubPreview";

afterEach(cleanup);

describe("cleanPreview", () => {
  it("去掉开头重复的标题和格式符号", () => {
    const p = cleanPreview("中电福富信息科技有限公司",
      "# 中电福富信息科技有限公司\n\n中电福富信息科技有限公司（简称**中电福富**）成立于1987年。\n\n| 项 | 值 |\n|---|---|\n| 注册资本 | 2.01亿 |");
    expect(p.startsWith("#")).toBe(false);
    expect(p).not.toMatch(/\*\*|\|/);
    expect(p.startsWith("中电福富信息科技有限公司（简称中电福富）成立于1987年")).toBe(true);
    expect(cleanPreview("产数研究院", "# 产数研究院 - 产数研究院牵头产业创新")).toBe("产数研究院牵头产业创新");
    expect(p).toContain("注册资本 2.01亿");
  });

  it("原件页: 不露 Upload 头 / SOURCE_PATH / 本机路径", () => {
    const p = cleanPreview("原件 · 中电福富工作汇报栾总--v165",
      "# Upload: 中电福富工作汇报（栾总） -v1.65.pptx SOURCE_PATH: x\n\nSOURCE_PATH: /Users/someone/Downloads/材料库/2024/汇报 -v1.65.pptx\nMETHOD: python-pptx\n\n## 第 1 页\n见 /Users/someone/Desktop/方案.pdf。");
    expect(p).not.toMatch(/Upload|SOURCE_PATH|METHOD|\/Users\//);
    expect(p.startsWith("第 1 页")).toBe(true);
    expect(p).toContain("「方案.pdf」");
  });

  it("hideLocalPaths: 留文件名, 网址和相对路径不动", () => {
    expect(hideLocalPaths("见 C:\\Users\\zhang\\Desktop\\方案.pdf 和 https://a.com/b.html 及 docs/a.md"))
      .toBe("见 「方案.pdf」 和 https://a.com/b.html 及 docs/a.md");
  });
});

describe("personLabel", () => {
  it("邮箱只留 @ 前面; 自己审自己写「已审」; 别人审写名字", () => {
    expect(personLabel("chenhongbo@ffcs.cn", "2 天前", "chenhongbo@ffcs.cn")).toBe("chenhongbo · 2 天前 · 已审");
    expect(personLabel("a@x.cn", "刚刚", "boss@x.cn")).toBe("a · 刚刚 · boss 审过");
    expect(personLabel("a@x.cn", "刚刚", null)).toBe("a · 刚刚");
  });
});

describe("WikiDocPreview", () => {
  it("按部门路径取全文, 正文里的本机路径不显示", async () => {
    me.fetchWithAuth.mockResolvedValue({ ok: true, json: async () => ({ body_md: "## 第 1 页\n\n见 /Users/x/Desktop/规范.pdf 第二节" }) });
    render(<WikiDocPreview namespace="dept/研发" fileId="abc" title="原件 · 规范" onClose={() => undefined} />);
    expect(await screen.findByText(/第二节/)).toBeTruthy();
    expect(me.fetchWithAuth).toHaveBeenCalledWith("http://gw/v1/wiki/documents/dept/%E7%A0%94%E5%8F%91/abc");
    expect(document.body.textContent).not.toContain("/Users/");
  });

  it("没权限说人话", async () => {
    me.fetchWithAuth.mockResolvedValue({ ok: false, status: 403 });
    render(<WikiDocPreview namespace="dept/研发" fileId="abc" title="t" onClose={() => undefined} />);
    expect(await screen.findByText("你没有权限看这一页")).toBeTruthy();
  });
});
