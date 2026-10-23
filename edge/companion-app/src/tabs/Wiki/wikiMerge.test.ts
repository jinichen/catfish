import { describe, expect, it } from "vitest";
import type { WikiFileFull, WikiFileInfo } from "../../lib/tauri_wiki";
import { mergeWikiEntries, renameWikiEntry } from "./wikiMerge";

function entry(info: Partial<WikiFileInfo>, fm: string, body: string): WikiFileFull {
  const full = {
    rel_path: "", title: "", kind: "entity", subtype: null, tags: [], related: [], sources: [], aliases: [],
    ...info,
  } as WikiFileInfo;
  return { info: full, content: `---\n${fm}\n---\n${body}`, frontmatter: fm, body };
}

// 10/23 实际情况: 同名的「公司治理体系概述」, 一条手动入库的实体 (文件), 一条蒸馏的概念 (体系)
const doc = entry(
  { rel_path: "wiki/entities/公司治理体系概述.md", title: "公司治理体系概述", kind: "entity", subtype: "document",
    aliases: ["中电福富治理体系"], tags: ["公司治理", "2023"], sources: ["manual"],
    related: [{ name: "中电福富信息科技有限公司", rel: "记录" }] },
  'type: entity\ntitle: 公司治理体系概述\nentity_type: document\naliases: [中电福富治理体系]\ntags: [公司治理, 2023]\nrelated: [{name: "中电福富信息科技有限公司", rel: "记录"}]\nsources: [manual]',
  "# 公司治理体系概述\n\n1页公司治理结构图。\n\n## 治理架构\n\n治理层级：股东 → 董事会 → 党委 / 经理层。\n",
);
const concept = entry(
  { rel_path: "wiki/concepts/公司治理体系概述.md", title: "公司治理体系概述", kind: "concept", subtype: "system",
    tags: ["治理", "公司治理"], sources: ["raw/sources/1-公司治理体系概述"], evidence: ["公司不设股东会"],
    related: [{ name: "中电福富信息科技有限公司", rel: "隶属" }, { name: "董事会", rel: "包含" }] },
  'type: concept\ntitle: 公司治理体系概述\nconcept_type: system\ntags: [治理, 公司治理]\nrelated: [{name: "中电福富信息科技有限公司", rel: "隶属"}, {name: "董事会", rel: "包含"}]\nsources: ["raw/sources/1-公司治理体系概述"]\nevidence: ["公司不设股东会"]',
  "# 公司治理体系概述\n\n- 股东直接行使职权，公司不设股东会。\n- 治理层级：股东 → 董事会 → 党委 / 经理层。\n",
);

describe("mergeWikiEntries", () => {
  it("保留一条: 来源、标签、证据取并集, 对方独有的关系补上, 同一对象的关系以保留的为准", () => {
    const out = mergeWikiEntries(doc, [concept]);
    expect(out).toMatch(/sources: \["manual", "raw\/sources\/1-公司治理体系概述"\]/);
    expect(out).toMatch(/tags: \["公司治理", "2023", "治理"\]/);
    expect(out).toContain('evidence: ["公司不设股东会"]');
    expect(out).toContain('{name: "中电福富信息科技有限公司", rel: "记录"}');
    expect(out).not.toContain("隶属");
    expect(out).toContain('{name: "董事会", rel: "包含"}');
    expect(out).toContain("type: entity");
  });

  it("正文只并入保留那条没有的行, 放在「并入自」小节下", () => {
    const out = mergeWikiEntries(doc, [concept]);
    expect(out).toContain("## 并入自「公司治理体系概述」\n\n- 股东直接行使职权，公司不设股东会。");
    expect(out.match(/治理层级/g)).toHaveLength(1);
  });

  it("名字不一样的 (别名撞了): 对方的名字记成别名, 写那个名字的关系照样连得上", () => {
    const other = entry({ ...concept.info, title: "中电福富治理体系", aliases: [] }, concept.frontmatter, concept.body);
    const out = mergeWikiEntries(doc, [other]);
    expect(out).toMatch(/aliases: \["中电福富治理体系"\]/);
  });

  it("没有新内容就不加小节", () => {
    expect(mergeWikiEntries(doc, [doc])).not.toContain("并入自");
  });
});

describe("renameWikiEntry", () => {
  it("改 title 和一级标题, 别的不动", () => {
    const out = renameWikiEntry(concept.content, "公司治理体系概述", "公司治理体系");
    expect(out).toContain("title: 公司治理体系\n");
    expect(out).toContain("# 公司治理体系\n");
    expect(out).toContain("- 股东直接行使职权");
  });
});
