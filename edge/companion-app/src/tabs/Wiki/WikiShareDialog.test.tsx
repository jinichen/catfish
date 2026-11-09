// @vitest-environment jsdom
/** 分享到部门对话框 (11/09): 先预检再发 —— 来源 / 关系同事能不能看到、警告一次列全、勾了什么就发什么。数据全是虚构的。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { WikiFileFull, WikiSharePlan } from "../../lib/tauri";

const t = vi.hoisted(() => ({ wikiSharePlan: vi.fn(), toolBridgeCallTool: vi.fn(), wikiSensitiveTermsEnsure: vi.fn() }));
vi.mock("../../lib/tauri", async (orig) => ({ ...(await orig<typeof import("../../lib/tauri")>()), ...t }));
vi.mock("../../lib/me", async (orig) => ({
  ...(await orig<typeof import("../../lib/me")>()),
  fetchMe: async () => ({ department: "研发部" }),
}));

import WikiShareDialog from "./WikiShareDialog";

const clean = { blocked: null, warnings: [] };
function makePlan(attached: string[]): WikiSharePlan {
  const linked = attached.includes("raw/sources/1-组织架构");
  return {
    ok: true, title: "市场部", rel_path: "wiki/entities/市场部.md", shared_before: false, hub_known: true, scan: clean,
    preview: {
      frontmatter_yaml: "title: 市场部",
      body_md: "# 市场部\n\n一级部。\n\n## 关联\n\n- **来源**: " + (linked ? "[[原件 · 组织架构]]" : "原件「组织架构」(在发布人那里, 没随这页分享)") + "\n- 另见 [[营销手册]]",
      fields: {
        title: "市场部", entity_type: "department", aliases: ["市场经营部"], tags: ["组织架构"],
        related: [{ name: "中电福富信息科技有限公司", rel: "隶属" }, { name: "销售单元", rel: "下辖" }],
        sources: [linked ? "原件 · 组织架构" : "原件「组织架构」(在发布人那里, 没随这页分享)"],
        evidence: ["毛利672万元...17.23%", "行业外收入 | 3600 | 239"],
      },
    },
    sources: [
      { ref: "raw/sources/1-组织架构", kind: "raw", label: "原件「组织架构」(在发布人那里, 没随这页分享)", key: "raw/sources/1-组织架构",
        title: "原件 · 组织架构", publish_path: "raw/sources/1-组织架构.md", size_bytes: 5931, attachable: true, shared: false,
        scan: { blocked: null, warnings: [{ category: "pii", hits: [1], advice: "" }] } },
      { ref: "journal:2026-08-15", kind: "conversation", label: "发布人的对话记录 (没随这页分享)" },
    ],
    relations: [
      { name: "中电福富信息科技有限公司", rel: "隶属", status: "local", rel_path: "wiki/entities/中电福富.md", title: "中电福富信息科技有限公司", scan: clean },
      { name: "销售单元", rel: "下辖", status: "shared" },
      { name: "营销手册", status: "missing" },
    ],
  };
}

const file = { info: { rel_path: "wiki/entities/市场部.md" } } as unknown as WikiFileFull;

beforeEach(() => {
  Object.values(t).forEach((f) => f.mockReset());
  t.wikiSharePlan.mockImplementation(async (_p: string, attached: string[]) => makePlan(attached));
  t.toolBridgeCallTool.mockResolvedValue({ ok: true, result: { ok: true } });
});
afterEach(cleanup);

describe("分享到部门", () => {
  it("预检: 来源、关系各自说清同事那边能不能看到; 对话记录不发", async () => {
    render(<WikiShareDialog selectedFile={file} onClose={() => {}} />);
    expect(await screen.findByText(/把原件「组织架构」也分享/)).toBeTruthy();
    expect(screen.getByText(/不勾: 同事那边显示「原件在发布人那里」/)).toBeTruthy();
    expect(screen.getByText("你的对话记录不会分享出去")).toBeTruthy();
    expect(screen.getByText("同事还看不到")).toBeTruthy();
    expect(screen.getByText("同事已经能看到")).toBeTruthy();
    expect(screen.getByText("你本机也没有这一页")).toBeTruthy();
    expect(await screen.findByText(/研发部同事马上能看到/)).toBeTruthy();   // 分享到哪个部门摆在眼前
    expect((screen.getByRole("radio", { name: /^本部门/ }) as HTMLInputElement).checked).toBe(true);   // 没发过 = 本部门
    // 原文依据 (从原件摘的原话) 会一起发出去: 没展开预览时也提醒一句
    expect(screen.getByText(/这一页带 2 条原文依据/)).toBeTruthy();
  });

  it("预览: 按知识页渲染, 关系 / 链接按同事点不点得开上色, 原文依据单独列出; 可以切到原始文本", async () => {
    render(<WikiShareDialog selectedFile={file} onClose={() => {}} />);
    fireEvent.click(await screen.findByText("看发出去的全文"));
    expect(screen.getByText("部门")).toBeTruthy();                       // 类型显示成中文
    expect(screen.getByText("市场经营部")).toBeTruthy();                 // 别名
    expect(screen.getByRole("heading", { name: "关联" })).toBeTruthy();   // 正文渲染了 markdown
    expect(screen.queryByText("# 市场部")).toBeNull();
    const reach = (name: string) => screen.getAllByText(name).find((e) => e.className.includes("wikilink"))!.className;
    expect(reach("销售单元")).toBe("wiki-preview__wikilink");                                   // 同事已经能看到
    expect(reach("中电福富信息科技有限公司")).toContain("wiki-preview__wikilink--dangling");     // 还没一起发
    expect(reach("营销手册")).toContain("wiki-preview__wikilink--dangling");
    fireEvent.click(screen.getByText("中电福富信息科技有限公司", { selector: "label *" }));
    await waitFor(() => expect(reach("中电福富信息科技有限公司")).toBe("wiki-preview__wikilink"));
    expect(screen.getByText(/原文依据 2 条/)).toBeTruthy();
    fireEvent.click(screen.getByText("看原始文本"));
    expect(screen.getByText(/^---/)).toBeTruthy();
  });

  it("什么都不勾: 只发这一页; 没勾「我知道」不能发", async () => {
    render(<WikiShareDialog selectedFile={file} onClose={() => {}} />);
    await screen.findByText(/把原件「组织架构」也分享/);
    const btn = screen.getByRole("button", { name: "确认分享" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    fireEvent.click(screen.getByText(/已经装走的撤不回/));
    await waitFor(() => expect(btn.disabled).toBe(false));
    fireEvent.click(btn);
    await waitFor(() => expect(t.toolBridgeCallTool).toHaveBeenCalledTimes(1));
    expect(t.toolBridgeCallTool.mock.calls[0]).toEqual(["catfish_wiki_publish",
      { wiki_rel_path: "wiki/entities/市场部.md", acknowledge_warnings: false, attached_sources: [], scope: "department" }]);
    expect(await screen.findByText(/「市场部」已分享/)).toBeTruthy();
  });

  it("勾原件 + 关联页: 预览里来源变成链接; 原件有警告要先确认; 依次发 3 页", async () => {
    render(<WikiShareDialog selectedFile={file} onClose={() => {}} />);
    fireEvent.click(await screen.findByText(/把原件「组织架构」也分享/));
    await waitFor(() => expect(t.wikiSharePlan).toHaveBeenLastCalledWith("wiki/entities/市场部.md", ["raw/sources/1-组织架构"]));
    fireEvent.click(screen.getByText("看发出去的全文"));
    // 11/09: 渲染后的样子 (不是 YAML): 原件一起发了 → 正文和来源里都是点得开的链接
    const docLink = (await screen.findAllByText("原件 · 组织架构")).find((e) => e.closest(".wiki-doc"))!;
    expect(docLink.className).toBe("wiki-preview__wikilink");
    expect(screen.queryByText(/^---/)).toBeNull();
    fireEvent.click(screen.getByText("中电福富信息科技有限公司", { selector: "label *" }));
    expect(screen.getByText("一起分享")).toBeTruthy();
    expect(screen.getByText(/「原件 · 组织架构」含 个人信息/)).toBeTruthy();
    fireEvent.click(screen.getByText(/已经装走的撤不回/));
    const btn = screen.getByRole("button", { name: "分享 3 页" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);   // 警告还没确认
    fireEvent.click(screen.getByText(/我看过这些内容/));
    await waitFor(() => expect(btn.disabled).toBe(false));
    fireEvent.click(btn);
    await waitFor(() => expect(t.toolBridgeCallTool).toHaveBeenCalledTimes(3));
    expect(t.toolBridgeCallTool.mock.calls.map((c) => (c[1] as { wiki_rel_path: string }).wiki_rel_path)).toEqual([
      "raw/sources/1-组织架构.md", "wiki/entities/中电福富.md", "wiki/entities/市场部.md",
    ]);
    expect((t.toolBridgeCallTool.mock.calls[2][1] as { attached_sources: string[] }).attached_sources).toEqual(["raw/sources/1-组织架构"]);
    expect((t.toolBridgeCallTool.mock.calls[2][1] as { acknowledge_warnings: boolean }).acknowledge_warnings).toBe(true);
  });

  it("全公司 (11/09): 关系按其他部门点不点得开重算; 换范围要重新勾「我知道」; 每页都带 scope=company", async () => {
    render(<WikiShareDialog selectedFile={file} onClose={() => {}} />);
    await screen.findByText(/把原件「组织架构」也分享/);
    fireEvent.click(screen.getByText(/已经装走的撤不回/));
    fireEvent.click(await screen.findByRole("radio", { name: /^全公司/ }));
    expect((screen.getByRole("button", { name: "确认分享" }) as HTMLButtonElement).disabled).toBe(true);
    // 销售单元只在部门里 (不是我发的) → 其他部门点不开
    expect(screen.getByText("只在别人的部门里, 其他部门点不开")).toBeTruthy();
    expect(screen.getByText(/本部门同事 \(管理员审过后全公司同事\)/)).toBeTruthy();
    fireEvent.click(screen.getByText(/已经装走的撤不回/));
    fireEvent.click(screen.getByRole("button", { name: "确认分享" }));
    await waitFor(() => expect(t.toolBridgeCallTool).toHaveBeenCalledTimes(1));
    expect((t.toolBridgeCallTool.mock.calls[0][1] as { scope: string }).scope).toBe("company");
    expect(await screen.findByText(/全公司等管理员审/)).toBeTruthy();
  });

  it("上次发到全公司: 默认选全公司, 说清管理员审到哪了", async () => {
    t.wikiSharePlan.mockImplementation(async (_p: string, attached: string[]) => ({
      ...makePlan(attached), shared_before: true, scope_before: "company",
      hub_status: { scope: "company", company_status: "rejected", company_note: "有经营数字" },
    }));
    render(<WikiShareDialog selectedFile={file} onClose={() => {}} />);
    expect(await screen.findByText(/上次申请全公司可见没通过: 有经营数字/)).toBeTruthy();
    await waitFor(() => expect((screen.getByRole("radio", { name: /^全公司/ }) as HTMLInputElement).checked).toBe(true));
  });

  it("某一页没发出去: 逐页说清", async () => {
    t.toolBridgeCallTool.mockResolvedValueOnce({ ok: true, result: { ok: false, error: "gateway 502" } });
    render(<WikiShareDialog selectedFile={file} onClose={() => {}} />);
    await screen.findByText(/把原件「组织架构」也分享/);
    fireEvent.click(screen.getByText(/已经装走的撤不回/));
    fireEvent.click(screen.getByRole("button", { name: "确认分享" }));
    expect(await screen.findByText(/「市场部」gateway 502/)).toBeTruthy();
  });

  it("预检失败 (客户档案): 说清原因, 不能发", async () => {
    t.wikiSharePlan.mockResolvedValue({ ok: false, error: "客户档案只存在你本机, 不能整页分享到部门" });
    render(<WikiShareDialog selectedFile={file} onClose={() => {}} />);
    expect(await screen.findByText(/客户档案只存在你本机/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "确认分享" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
