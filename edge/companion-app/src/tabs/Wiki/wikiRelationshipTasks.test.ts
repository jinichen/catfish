import { describe, expect, it } from "vitest";
import type { WikiFileInfo } from "../../lib/tauri_wiki";
import {
  buildConfirmedWikiContent,
  buildWikiRelationshipTasks,
  hasLegacyWikiRelations,
} from "./wikiRelationshipTasks";

function file(overrides: Partial<WikiFileInfo>): WikiFileInfo {
  return {
    rel_path: "wiki/entities/example.md",
    kind: "entity",
    slug: "example",
    title: "示例",
    subtype: "project",
    tags: [],
    related: [],
    sources: [],
    aliases: [],
    size_bytes: 10,
    mtime: 1,
    ontology_status: "active",
    ...overrides,
  };
}

describe("buildWikiRelationshipTasks", () => {
  it("分别生成待确认和缺少关系任务", () => {
    const tasks = buildWikiRelationshipTasks([
      file({ rel_path: "wiki/entities/pending.md", title: "待确认", ontology_status: "pending" }),
      file({ rel_path: "wiki/entities/orphan.md", title: "孤立项" }),
      file({ rel_path: "wiki/entities/linked.md", title: "已关联", related: [{ name: "孤立项", rel: "关联" }] }),
    ]);
    expect(tasks.map((task) => task.kind)).toEqual(["pending", "missing"]);
  });

  it("把标题和别名冲突合并成一个重复组", () => {
    const tasks = buildWikiRelationshipTasks([
      file({ rel_path: "wiki/entities/a.md", title: "中电福富", aliases: ["FFCS"], related: [{ name: "组织", rel: "属于" }] }),
      file({ rel_path: "wiki/entities/b.md", title: "FFCS", related: [{ name: "组织", rel: "属于" }] }),
    ]);
    const duplicates = tasks.filter((task) => task.kind === "duplicate");
    expect(duplicates).toHaveLength(1);
    expect(duplicates[0].duplicatePaths).toHaveLength(2);
  });

  it("把断链和缺少关系类型拆成可逐条修复的任务", () => {
    const tasks = buildWikiRelationshipTasks([
      file({
        rel_path: "wiki/entities/broken.md",
        title: "问题条目",
        related: [
          { name: "不存在的目标", rel: "依据" },
          { name: "示例" },
        ],
      }),
      file({ rel_path: "wiki/entities/target.md", title: "示例" }),
    ]);
    const broken = tasks.filter((task) => task.kind === "broken");
    expect(broken).toHaveLength(2);
    expect(broken.map((task) => task.relationName)).toEqual(["不存在的目标", "示例"]);
    expect(broken[0].detail).toContain("找不到目标条目");
    expect(broken[1].detail).toContain("缺少关系类型");
  });

  it("正常的 typed relation 不生成异常任务", () => {
    const tasks = buildWikiRelationshipTasks([
      file({ related: [{ name: "目标", rel: "依据" }] }),
      file({ rel_path: "wiki/entities/target.md", title: "目标" }),
    ]);
    expect(tasks.filter((task) => task.kind === "broken")).toHaveLength(0);
  });

  it("不把正文引用当成关系异常", () => {
    const tasks = buildWikiRelationshipTasks([
      file({ related: [{ name: "目标", source: "body" }] }),
      file({ rel_path: "wiki/entities/target.md", title: "目标" }),
    ]);
    expect(tasks.filter((task) => task.kind === "broken")).toHaveLength(0);
  });
});

describe("hasLegacyWikiRelations", () => {
  it("只把缺少关系类型的 frontmatter 关系识别为旧格式", () => {
    expect(hasLegacyWikiRelations([file({ related: [{ name: "目标" }] })])).toBe(true);
    expect(hasLegacyWikiRelations([file({ related: [{ name: "目标", source: "body" }] })])).toBe(false);
    expect(hasLegacyWikiRelations([file({ related: [{ name: "目标", rel: "关联" }] })])).toBe(false);
  });
});

describe("buildConfirmedWikiContent", () => {
  it("写入结构化关系并把待确认状态改为 active", () => {
    const source = "---\ntitle: 示例\nrelated: []\nontology_status: pending\n---\n\n正文\n";
    const result = buildConfirmedWikiContent(source, [{ name: "产品研发部", rel: "所属部门" }]);
    expect(result).toContain('related: [{name: "产品研发部", rel: "所属部门"}]');
    expect(result).toContain("ontology_status: active");
    expect(result).toContain("\n正文\n");
  });

  it("写回关系时不把正文引用升级成 frontmatter 关系", () => {
    const source = "---\ntitle: 示例\nrelated: []\n---\n\n正文含 [[目标]]\n";
    const result = buildConfirmedWikiContent(source, [
      { name: "目标", source: "body" },
      { name: "部门", rel: "所属部门", source: "frontmatter" },
    ]);
    expect(result).toContain('related: [{name: "部门", rel: "所属部门"}]');
    expect(result).toContain("正文含 [[目标]]");
  });

  it("兼容 CRLF frontmatter", () => {
    const source = "---\r\ntitle: 示例\r\nrelated: []\r\n---\r\n\r\n正文\r\n";
    const result = buildConfirmedWikiContent(source, [{ name: "部门", rel: "所属部门" }]);
    expect(result).toContain('related: [{name: "部门", rel: "所属部门"}]');
    expect(result).toContain("\n正文\r\n");
  });

  it("用户确认关系时为无 frontmatter 的历史条目补齐最小元数据", () => {
    const result = buildConfirmedWikiContent(
      "# 历史条目\n\n正文\n",
      [{ name: "部门", rel: "所属部门" }],
      file({ title: "历史条目", subtype: null, tags: ["历史"], sources: ["legacy"] }),
    );
    expect(result).toContain('type: entity');
    expect(result).toContain('title: "历史条目"');
    expect(result).toContain('related: [{name: "部门", rel: "所属部门"}]');
    expect(result).toContain("# 历史条目");
  });
});
