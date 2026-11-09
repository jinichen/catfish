/** 分享到部门: 勾了什么 → 依次发哪几页、要不要先确认警告 (11/09)。数据全是虚构的。 */
import { describe, expect, it } from "vitest";

import type { WikiSharePlan } from "./tauri_wiki";
import { attachedKeys, scopePlan, shareIssues, shareSteps, warningLabel } from "./wikiShare";
import { resolveRefForPage, type SharedPageRef } from "./wikiResolve";
import type { WikiFileInfo } from "./tauri";

const clean = { blocked: null, warnings: [] };
const plan: WikiSharePlan = {
  ok: true,
  title: "市场部",
  rel_path: "wiki/entities/市场部.md",
  shared_before: false,
  hub_known: true,
  scan: clean,
  preview: { frontmatter_yaml: "title: 市场部", body_md: "正文" },
  sources: [
    { ref: "raw/sources/1-组织架构", kind: "raw", label: "原件「组织架构」…", key: "raw/sources/1-组织架构", title: "原件 · 组织架构",
      publish_path: "raw/sources/1-组织架构.md", attachable: true, shared: false,
      scan: { blocked: null, warnings: [{ category: "pii", hits: [1, 2], advice: "" }] } },
    { ref: "raw/sources/2-旧规范", kind: "raw", label: "…", key: "raw/sources/2-旧规范", title: "原件 · 旧规范",
      publish_path: "raw/sources/2-旧规范.md", attachable: true, shared: true, scan: clean },
    { ref: "journal:2026-08-15", kind: "conversation", label: "发布人的对话记录 (没随这页分享)" },
  ],
  relations: [
    { name: "中电福富", status: "local", rel_path: "wiki/entities/中电福富.md", title: "中电福富", scan: clean },
    { name: "账号表", status: "local", rel_path: "wiki/entities/账号表.md", title: "账号表", scan: { blocked: "含疑似凭据", warnings: [] } },
    { name: "销售单元", status: "shared" },
    { name: "营销手册", status: "missing" },
  ],
};

describe("shareSteps", () => {
  it("什么都没勾: 只发这一页; 部门里已有的原件照样改成链接", () => {
    const steps = shareSteps(plan, { attach: new Set(), together: new Set() }, false);
    expect(steps.map((s) => [s.kind, s.label])).toEqual([["main", "市场部"]]);
    expect(steps[0].args).toEqual({
      wiki_rel_path: "wiki/entities/市场部.md", acknowledge_warnings: false, attached_sources: ["raw/sources/2-旧规范"],
      scope: "department",
    });
  });

  it("勾了原件和关联页: 原件先发, 再关联页, 最后这一页; 每页都带同一份 attached_sources", () => {
    const steps = shareSteps(plan, {
      attach: new Set(["raw/sources/1-组织架构"]),
      together: new Set(["wiki/entities/中电福富.md"]),
    }, true);
    expect(steps.map((s) => s.kind)).toEqual(["source", "related", "main"]);
    expect(steps[0].args).toEqual({ wiki_rel_path: "raw/sources/1-组织架构.md", acknowledge_warnings: true, scope: "department" });
    const attached = ["raw/sources/1-组织架构", "raw/sources/2-旧规范"];
    expect(steps[1].args.attached_sources).toEqual(attached);
    expect(steps[2].args.attached_sources).toEqual(attached);
    expect(attachedKeys(plan, { attach: new Set(["raw/sources/1-组织架构"]), together: new Set() })).toEqual(attached);
  });

  it("只有「本机有」的关联页能一起发: 已分享 / 本机没有的勾了也不算", () => {
    const steps = shareSteps(plan, { attach: new Set(), together: new Set(["销售单元", "营销手册"]) }, false);
    expect(steps).toHaveLength(1);
  });
});

describe("全公司 (11/09)", () => {
  // 销售单元: 我发过、只在部门里; 北部分公司: 全公司可见; 旧规范原件: 部门里有、全公司没有
  const p2: WikiSharePlan = {
    ...plan,
    relations: [
      ...plan.relations,
      { name: "北部分公司", status: "shared", company: true },
      { name: "华南单元", status: "shared", company: false, mine: false },
    ].map((r) => (r.name === "销售单元" ? { ...r, mine: true, rel_path: "wiki/entities/销售单元.md", company: false, scan: clean } : r)) as WikiSharePlan["relations"],
  };

  it("部门范围: 预检原样", () => {
    expect(scopePlan(p2, "department")).toBe(p2);
  });

  it("全公司: 只有全公司可见的算点得开; 我发过的部门页可以一起重发; 别人部门里的点不开", () => {
    const c = scopePlan(p2, "company");
    const st = Object.fromEntries(c.relations.map((r) => [r.name, r.status]));
    expect(st).toMatchObject({ 北部分公司: "shared", 销售单元: "local", 华南单元: "dept_only", 中电福富: "local", 营销手册: "missing" });
    expect(c.sources.find((s) => s.key === "raw/sources/2-旧规范")!.shared).toBe(false);
  });

  it("全公司: 每一页都带 scope=company; 部门里有、全公司没有的原件不再自动当链接", () => {
    const c = scopePlan(p2, "company");
    const steps = shareSteps(c, {
      attach: new Set(["raw/sources/2-旧规范"]),
      together: new Set(["wiki/entities/销售单元.md"]),
    }, false, "company");
    expect(steps.map((s) => [s.kind, s.args.scope])).toEqual([["source", "company"], ["related", "company"], ["main", "company"]]);
    expect(steps[2].args.attached_sources).toEqual(["raw/sources/2-旧规范"]);
    expect(attachedKeys(c, { attach: new Set(), together: new Set() })).toEqual([]);
  });
});

describe("shareIssues", () => {
  it("警告和拒发分开列: 含凭据的那页不能发", () => {
    const steps = shareSteps(plan, {
      attach: new Set(["raw/sources/1-组织架构"]),
      together: new Set(["wiki/entities/账号表.md"]),
    }, false);
    const { warned, blocked } = shareIssues(steps);
    expect(warned.map((w) => w.label)).toEqual(["原件 · 组织架构"]);
    expect(warningLabel(warned[0].warnings[0])).toBe("个人信息 (身份证 / 手机 / 工号 / 银行卡) 2 处");
    expect(blocked).toEqual([{ label: "账号表", reason: "含疑似凭据" }]);
  });
});

describe("resolveRefForPage (同事看装进来的页)", () => {
  const files = [{ rel_path: "wiki/entities/中电福富.md", title: "中电福富", aliases: [], slug: "zdff" }] as unknown as WikiFileInfo[];
  const shared: SharedPageRef[] = [
    { relPath: "wiki-shared/dept/研发部/a.md", title: "市场部", namespace: "dept/研发部", publishedBy: "alice@x.cn" },
    { relPath: "wiki-shared/dept/研发部/b.md", title: "中电福富", namespace: "dept/研发部", publishedBy: "alice@x.cn" },
    { relPath: "wiki-shared/dept/研发部/c.md", title: "中电福富", namespace: "dept/研发部", publishedBy: "bob@x.cn" },
    { relPath: "wiki-shared/dept/研发部/d.md", title: "原件 · 组织架构", namespace: "dept/研发部", publishedBy: "alice@x.cn" },
  ];

  it("先找同一个发布人分享的同名页 (不连到自己那条, 也不连到别人分享的同名页)", () => {
    expect(resolveRefForPage("中电福富", "wiki-shared/dept/研发部/a.md", files, shared)).toBe("wiki-shared/dept/研发部/b.md");
    expect(resolveRefForPage("原件 · 组织架构", "wiki-shared/dept/研发部/a.md", files, shared)).toBe("wiki-shared/dept/研发部/d.md");
  });

  it("同一个人没分享这页: 退回自己知识库里的; 看自己的页时跟原来一样", () => {
    expect(resolveRefForPage("中电福富", "wiki-shared/dept/研发部/x.md", files, shared)).toBe("wiki/entities/中电福富.md");
    expect(resolveRefForPage("中电福富", "wiki/entities/市场部.md", files, shared)).toBe("wiki/entities/中电福富.md");
    expect(resolveRefForPage("营销手册", "wiki-shared/dept/研发部/a.md", files, shared)).toBeNull();
  });
});
