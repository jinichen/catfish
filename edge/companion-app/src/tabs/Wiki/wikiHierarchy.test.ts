import { describe, expect, it } from "vitest";
import contract from "../../../../contracts/wiki_relation_vocab.json";
import type { WikiFileInfo } from "../../lib/tauri_wiki";
import { BUILTIN_HIERARCHY, buildHierarchy, hierarchyIssues } from "./wikiHierarchy";

const f = (slug: string, title: string, related: { name: string; rel: string }[] = [], aliases: string[] = []): WikiFileInfo => ({
  rel_path: `wiki/entities/${slug}.md`, kind: "entity", slug, title, subtype: null, tags: [], related, sources: [],
  aliases, size_bytes: 0, mtime: 0,
});

describe("buildHierarchy", () => {
  it("隶属 / 包含 两个方向都拼进同一棵树; 顶层按下级多少排", () => {
    const files = [
      f("co", "中电福富", [{ name: "人力资源部", rel: "包含" }]),
      f("hr", "人力资源部"),
      f("p", "傅岱松", [{ name: "人力资源部", rel: "隶属" }]),
      f("ccrc", "CCRC"),
      f("sub", "CCRC 应急二级", [{ name: "CCRC", rel: "隶属" }]),
      f("x", "无关", [{ name: "中电福富", rel: "协作" }]),
    ];
    const m = buildHierarchy(files);
    expect(m.roots.map((r) => [r.path, r.size])).toEqual([["wiki/entities/co.md", 2], ["wiki/entities/ccrc.md", 1]]);
    expect(m.children.get("wiki/entities/hr.md")).toEqual(["wiki/entities/p.md"]);
    expect(m.parents.has("wiki/entities/x.md")).toBe(false);
  });

  it("自定义的层级型关系也算", () => {
    const m = buildHierarchy([f("a", "挂靠人", [{ name: "公司", rel: "挂靠" }]), f("b", "公司")], { 挂靠: "child" });
    expect(m.roots[0].path).toBe("wiki/entities/b.md");
  });

  it("环不会死循环", () => {
    const m = buildHierarchy([f("a", "A", [{ name: "B", rel: "隶属" }]), f("b", "B", [{ name: "A", rel: "隶属" }])]);
    expect(m.roots).toEqual([]);
  });

  it("内置层级关系跟 contracts 一致", () => {
    expect(BUILTIN_HIERARCHY).toEqual((contract as { hierarchy: Record<string, string> }).hierarchy);
  });
});

describe("hierarchyIssues", () => {
  it("跨级重复只标直挂祖先那条; 剩下 ≥2 个上级才算多个上级", () => {
    const files = [
      f("co", "公司"),
      f("dept", "部门", [{ name: "公司", rel: "隶属" }]),
      f("p", "张三", [{ name: "部门", rel: "隶属" }, { name: "公司", rel: "隶属" }]),
      f("c1", "体系"),
      f("c2", "口径"),
      f("cat", "分类", [{ name: "体系", rel: "隶属" }, { name: "口径", rel: "隶属" }]),
    ];
    const issues = hierarchyIssues(buildHierarchy(files));
    const skip = issues.filter((i) => i.kind === "skiplevel");
    expect(skip).toHaveLength(1);
    expect(skip[0].links[0]).toMatchObject({ parent: "wiki/entities/co.md", child: "wiki/entities/p.md", declaredOn: "wiki/entities/p.md" });
    expect(skip[0].via).toBe("wiki/entities/dept.md");
    const multi = issues.filter((i) => i.kind === "multiparent");
    expect(multi.map((i) => i.child)).toEqual(["wiki/entities/cat.md"]);
  });
});
