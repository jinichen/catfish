import { describe, expect, it } from "vitest";
import { buildUserPrompt, sanitizeSuggestions } from "./wikiRelationSuggest";
import type { WikiFileInfo } from "./tauri";

const info = (over: Partial<WikiFileInfo>): WikiFileInfo => ({
  rel_path: "wiki/entities/x.md", kind: "entity", slug: "x", title: "x", subtype: null, tags: [], related: [],
  sources: [], aliases: [], size_bytes: 0, mtime: 0, ...over,
});

describe("sanitizeSuggestions", () => {
  it("只留词表里的类型、只留问到的名字, 每个名字一条", () => {
    const out = sanitizeSuggestions({
      suggestions: [
        { name: "市场部", rel: "包含", reason: "公司包含部门" },
        { name: "市场部", rel: "协作", reason: "重复的第二条" },
        { name: "金智达", rel: "合作伙伴", reason: "词表外" },
        { name: "编出来的", rel: "隶属", reason: "没问过" },
        { name: "CMMI-5", rel: "关联", reason: "应在对方条目记" },
      ],
    }, ["市场部", "金智达", "CMMI-5"]);
    expect(out).toEqual([
      { name: "市场部", rel: "包含", reason: "公司包含部门" },
      { name: "CMMI-5", rel: "关联", reason: "应在对方条目记" },
    ]);
  });

  it("reverse 只在真 true 且不是「关联」时留下", () => {
    const out = sanitizeSuggestions({
      suggestions: [
        { name: "中电福富", rel: "持有", reason: "公司持有本证书", reverse: true },
        { name: "CMMI", rel: "关联", reason: "拿不准", reverse: true },
        { name: "市场部", rel: "包含", reason: "x", reverse: "yes" },
      ],
    }, ["中电福富", "CMMI", "市场部"]);
    expect(out).toEqual([
      { name: "中电福富", rel: "持有", reason: "公司持有本证书", reverse: true },
      { name: "CMMI", rel: "关联", reason: "拿不准" },
      { name: "市场部", rel: "包含", reason: "x" },
    ]);
  });

  it("结构不对就空", () => {
    expect(sanitizeSuggestions(null, ["a"])).toEqual([]);
    expect(sanitizeSuggestions({ suggestions: "x" }, ["a"])).toEqual([]);
  });
});

describe("buildUserPrompt", () => {
  it("带上对方的类型和标签, 找不到的写明", () => {
    const entry = info({ title: "中电福富", subtype: "org" });
    const dept = info({ title: "市场部", subtype: "department", tags: ["部门"] });
    const p = buildUserPrompt(entry, "公司简介", ["市场部", "不存在"], [entry, dept]);
    expect(p).toContain("本条目: 中电福富 (实体·org)");
    expect(p).toContain("- 市场部 (实体·department) 标签: 部门");
    expect(p).toContain("- 不存在 (知识库里没找到这个条目)");
  });
});
