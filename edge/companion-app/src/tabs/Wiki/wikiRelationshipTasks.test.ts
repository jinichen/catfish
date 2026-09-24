import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { WikiFileInfo } from "../../lib/tauri_wiki";
import {
  RELATION_FALLBACK,
  RELATION_VOCAB,
  buildConfirmedWikiContent,
  buildWikiRelationshipTasks,
  cleanPendingFiles,
  conflictFieldLabel,
  hasLegacyWikiRelations,
  relationTypeOptions,
  renameRelationTarget,
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
  it("9/24: 没有关系不再算任务 —— 只有待确认条目要人看", () => {
    const tasks = buildWikiRelationshipTasks([
      file({ rel_path: "wiki/entities/pending.md", title: "待确认", ontology_status: "pending" }),
      file({ rel_path: "wiki/entities/orphan.md", title: "孤立项" }),
      file({ rel_path: "wiki/entities/linked.md", title: "已关联", related: [{ name: "孤立项", rel: "关联" }] }),
    ]);
    expect(tasks.map((task) => task.kind)).toEqual(["pending"]);
    expect(tasks[0].detail).toContain("关系可选");
  });

  it("9/24: 同一个名字指向不明, 不管多少处引用只算一条任务", () => {
    const company = file({ rel_path: "wiki/entities/company.md", title: "中电福富信息科技有限公司", aliases: ["中电福富"] });
    const dup = file({ rel_path: "wiki/entities/fufu.md", title: "福富", aliases: ["中电福富"] });
    const refs = Array.from({ length: 24 }, (_, i) =>
      file({ rel_path: `wiki/concepts/c${i}.md`, title: `规则${i}`, related: [{ name: "中电福富", rel: "隶属" }] }));
    const tasks = buildWikiRelationshipTasks([company, dup, ...refs]);
    const ambiguous = tasks.filter((task) => task.kind === "ambiguous");
    expect(ambiguous).toHaveLength(1);
    expect(ambiguous[0].refs).toHaveLength(24);
    expect(ambiguous[0].candidates?.map((c) => c.title).sort()).toEqual(["中电福富信息科技有限公司", "福富"].sort());
    expect(tasks.some((task) => task.kind === "broken")).toBe(false);
  });

  it("9/24: 待确认条目自己的关系问题不另开任务, 在工作台里逐条标", () => {
    const tasks = buildWikiRelationshipTasks([
      file({ rel_path: "wiki/concepts/p.md", title: "新流程", ontology_status: "pending", related: [{ name: "不存在", rel: "依据" }] }),
    ]);
    expect(tasks.map((task) => task.kind)).toEqual(["pending"]);
    expect(tasks[0].detail).toContain("1 条要改");
  });

  it("9/24: 目标是待确认条目也算连得上 (同一批互相引用)", () => {
    const tasks = buildWikiRelationshipTasks([
      file({ rel_path: "wiki/concepts/a.md", title: "材料版本控制流程", ontology_status: "pending", related: [{ name: "终验报告归档规则", rel: "配套" }] }),
      file({ rel_path: "wiki/concepts/b.md", title: "终验报告归档规则", ontology_status: "pending" }),
    ]);
    expect(tasks.every((task) => task.kind === "pending")).toBe(true);
    expect(cleanPendingFiles(tasks.map((task) => task.file)).map((f) => f.title).sort())
      .toEqual(["材料版本控制流程", "终验报告归档规则"].sort());
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
    expect(broken[0].detail).toContain("找不到这个条目");
    expect(broken[1].detail).toContain("没写关系类型");
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
  it("9/24: keepStatus —— 只改关系 (删/加/改名) 不等于员工核对过, 待确认状态原样", () => {
    const source = "---\ntitle: 示例\nrelated: [{name: \"A\", rel: \"依据\"}]\nontology_status: pending\n---\n\n正文\n";
    const result = buildConfirmedWikiContent(source, [], undefined, { keepStatus: true });
    expect(result).toContain("related: []");
    expect(result).toContain("ontology_status: pending");
  });

  it("9/24: renameRelationTarget 只改写了这个名字的关系, 正文引用不动", () => {
    const next = renameRelationTarget(
      [{ name: "中电福富", rel: "隶属" }, { name: "中电福富", source: "body" }, { name: "B", rel: "依据" }],
      "中电福富",
      "中电福富信息科技有限公司",
    );
    expect(next.map((r) => r.name)).toEqual(["中电福富信息科技有限公司", "中电福富", "B"]);
  });

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

  it("9/17: 原样确认现有候选关系 —— 5 条不增不减不改, 只把 pending 翻成 active", () => {
    // 现场: 小鲶批量建的条目互相引用, 每条都 pending; 员工核对后候选全对, 只想盖章。
    const related = [
      { name: "通信工程施工总承包(二级)", rel: "关联" },
      { name: "机电工程施工总承包(二级)", rel: "关联" },
      { name: "建筑装修装饰工程专业承包-二级", rel: "关联" },
      { name: "消防设施工程专业承包-二级", rel: "关联" },
      { name: "业绩合同额与承包范围口径", rel: "关联" },
    ];
    const serialized = related.map((r) => `{name: "${r.name}", rel: "${r.rel}"}`).join(", ");
    const source = `---\ntype: concept\nontology_status: pending\ntitle: 闽建许161号\nrelated: [${serialized}]\nsources: [manual]\n---\n\n# 正文\n`;
    const result = buildConfirmedWikiContent(source, related);
    expect(result).toContain("ontology_status: active");
    expect(result).toContain(`related: [${serialized}]`);
    expect(result).not.toContain("pending");
    expect(result.endsWith("\n# 正文\n")).toBe(true);
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

describe("关系词表 (9/17)", () => {
  it("跟 edge/contracts/wiki_relation_vocab.json 一字不差 —— 三条产线同一份词表", () => {
    const contract = JSON.parse(
      readFileSync(resolve(__dirname, "../../../../contracts/wiki_relation_vocab.json"), "utf-8"),
    ) as { fallback: string; relations: Record<string, string> };
    expect([...RELATION_VOCAB]).toEqual(Object.keys(contract.relations));
    expect(RELATION_FALLBACK).toBe(contract.fallback);
  });

  it("下拉只给词表 + 兜底, 库里的自造词不再出现; 兜底在最后", () => {
    const options = relationTypeOptions([
      file({ related: [{ name: "A", rel: "持有主体", source: "frontmatter" }] }),
    ]);
    expect(options).not.toContain("持有主体");
    expect(options[0]).toBe("隶属");
    expect(options.at(-1)).toBe(RELATION_FALLBACK);
  });
});

describe("冲突任务 (9/17, semantica 第 2 条)", () => {
  it("frontmatter conflicts 每条一个任务, 排在最前, 带两个值", () => {
    const tasks = buildWikiRelationshipTasks([
      file({ rel_path: "wiki/entities/pending.md", title: "待确认", ontology_status: "pending" }),
      file({
        rel_path: "wiki/entities/x.md",
        title: "中电福富",
        related: [{ name: "A", rel: "隶属" }],
        conflicts: [
          { field: "entity_type", current: "org", proposed: "department", seen: "journal:2026-09-17" },
          { field: "rel:A", current: "隶属", proposed: "协作", seen: "" },
        ],
      }),
    ]);
    expect(tasks.slice(0, 2).map((task) => task.kind)).toEqual(["conflict", "conflict"]);
    expect(tasks[0].title).toBe("「中电福富」的类型有两个说法");
    expect(tasks[0].detail).toContain("org");
    expect(tasks[0].detail).toContain("journal:2026-09-17");
    expect(tasks[1].title).toBe("「中电福富」的与「A」的关系有两个说法");
    expect(tasks[1].conflict?.proposed).toBe("协作");
  });

  it("没有 conflicts 字段的老条目不生成冲突任务", () => {
    const tasks = buildWikiRelationshipTasks([file({ related: [{ name: "A", rel: "隶属" }] })]);
    expect(tasks.some((task) => task.kind === "conflict")).toBe(false);
  });

  it("字段名翻译成员工看得懂的", () => {
    expect(conflictFieldLabel("entity_type")).toBe("类型");
    expect(conflictFieldLabel("concept_type")).toBe("类型");
    expect(conflictFieldLabel("rel:中电福富")).toBe("与「中电福富」的关系");
  });
});
