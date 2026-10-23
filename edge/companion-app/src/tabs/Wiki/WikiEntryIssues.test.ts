import { describe, expect, it } from "vitest";
import type { WikiFileInfo } from "../../lib/tauri_wiki";
import { entryTasks } from "./WikiEntryIssues";
import { buildWikiRelationshipTasks } from "./wikiRelationshipTasks";

const f = (rel: string, title: string, over: Partial<WikiFileInfo> = {}): WikiFileInfo => ({
  rel_path: rel, kind: "entity", slug: title, title, subtype: "doc", tags: [], related: [], sources: [],
  aliases: [], size_bytes: 0, mtime: 0, ...over,
});

describe("条目页的待处理事项", () => {
  it("只列本条目的; 批量的「没标类型」「可能重复」不在单条目页列", () => {
    const plan = f("wiki/entities/规划.md", "十五五规划", {
      ontology_status: "pending",
      related: [{ name: "省政府办公厅", rel: "隶属" }, { name: "某某", rel: "关联" }],
      unverified: ["纲领性文件 —— 摘录原文里找不到「…」"],
    });
    const other = f("wiki/entities/别的.md", "别的", { ontology_status: "pending" });
    const tasks = buildWikiRelationshipTasks([plan, other]);
    const mine = entryTasks(tasks, plan.rel_path);
    expect(mine.map((t) => t.kind).sort()).toEqual(["pending", "unverified"]);
  });

  it("事实一条都没对上原文的待确认, 说明里讲清楚原因", () => {
    const plan = f("wiki/entities/规划.md", "十五五规划", {
      ontology_status: "pending", unverified: ["a —— 原因", "b —— 原因"], evidence: [],
    });
    const pending = buildWikiRelationshipTasks([plan]).find((t) => t.kind === "pending")!;
    expect(pending.detail).toContain("2 条事实都没对上原文");
  });
});
