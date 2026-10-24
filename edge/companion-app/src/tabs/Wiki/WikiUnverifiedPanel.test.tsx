// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { WikiFileInfo } from "../../lib/tauri_wiki";

const writes: [string, string][] = [];
let content = "";
const info = (unverified: string[]): WikiFileInfo => ({
  rel_path: "wiki/concepts/规划.md", kind: "concept", slug: "规划", title: "规划", subtype: "政策", tags: [],
  related: [], sources: [], aliases: [], size_bytes: 0, mtime: 0, unverified, evidence: ["到2030年比重达到35%左右"],
});

vi.mock("../../lib/tauri_wiki", () => ({
  wikiReadFile: vi.fn(async () => ({ info: current, content, frontmatter: "", body: "" })),
  wikiUpdateFile: vi.fn(async (rel: string, c: string) => { writes.push([rel, c]); }),
}));
vi.mock("../../store/wiki", () => ({
  useWikiStore: (sel: (s: { loadFiles: () => Promise<void> }) => unknown) => sel({ loadFiles: async () => {} }),
}));
let current: WikiFileInfo;

import WikiUnverifiedPanel, { appendFact, setFrontmatterList, splitUnverified } from "./WikiUnverifiedPanel";
import { buildWikiRelationshipTasks } from "./wikiRelationshipTasks";

const ITEMS = ["新材料破万亿 —— 摘录原文里找不到「突破1万亿元」", "研发增长 15% —— 数字原文里没有: 15"];

describe("WikiUnverifiedPanel", () => {
  beforeEach(() => {
    cleanup();
    writes.length = 0;
    current = info(ITEMS);
    content = `---\ntitle: 规划\nontology_status: pending\nunverified: ["${ITEMS[0]}", "${ITEMS[1]}"]\n---\n\n# 规划\n\n- 已有事实\n`;
  });

  it("有 unverified 的条目列成任务", () => {
    const tasks = buildWikiRelationshipTasks([current]);
    const t = tasks.find((x) => x.kind === "unverified");
    expect(t?.title).toContain("2 条没对上原文");
  });

  it("加回 = 正文追加这条, 列表删掉它; 状态不动", async () => {
    const task = buildWikiRelationshipTasks([current]).find((x) => x.kind === "unverified")!;
    render(<WikiUnverifiedPanel task={task} onLater={null} />);
    fireEvent.click(screen.getAllByText("确实对，加回正文")[0]);
    await waitFor(() => expect(writes).toHaveLength(1));
    const out = writes[0][1];
    expect(out).toMatch(/- 新材料破万亿\n$/);
    expect(out).not.toContain("突破1万亿元");
    expect(out).toContain("研发增长 15%");
    expect(out).toContain("ontology_status: pending");
  });

  it("丢掉 = 只删列表, 正文不加", async () => {
    const task = buildWikiRelationshipTasks([current]).find((x) => x.kind === "unverified")!;
    render(<WikiUnverifiedPanel task={task} onLater={null} />);
    fireEvent.click(screen.getAllByText("丢掉")[1]);
    await waitFor(() => expect(writes).toHaveLength(1));
    const out = writes[0][1];
    expect(out).not.toContain("研发增长 15%");
    expect(out).toContain("新材料破万亿 —— ");
  });

  it("核对不过的关系: 加回 = 放回 related, 正文不动", async () => {
    const rel = "关系：「规划」隶属「福建省人民政府」 —— 原文对不上 (不支持: 原文是印发)";
    current = info([rel]);
    content = `---\ntitle: 规划\nontology_status: active\nrelated: [{name: "省发改委", rel: "关联"}]\nunverified: ["${rel}"]\n---\n\n# 规划\n\n- 已有事实\n`;
    current.related = [{ name: "省发改委", rel: "关联" }];
    const task = buildWikiRelationshipTasks([current]).find((x) => x.kind === "unverified")!;
    render(<WikiUnverifiedPanel task={task} onLater={null} />);
    fireEvent.click(screen.getByText("确实对，加回关系"));
    await waitFor(() => expect(writes).toHaveLength(1));
    const out = writes[0][1];
    expect(out).toContain('{name: "福建省人民政府", rel: "隶属"}');
    expect(out).toContain('{name: "省发改委", rel: "关联"}');
    expect(out).not.toContain("unverified");
    expect(out).toMatch(/- 已有事实\n$/);
    expect(out).toContain("ontology_status: active");
  });

  it("helpers", () => {
    expect(splitUnverified("a —— b —— 原因")).toEqual({ fact: "a —— b", reason: "原因" });
    expect(setFrontmatterList("---\ntitle: x\nunverified: [\"a\"]\n---\nbody", "unverified", [])).toBe("---\ntitle: x\n---\nbody");
    expect(appendFact("---\n---\n\n正文\n\n", "新")).toBe("---\n---\n\n正文\n- 新\n");
  });
});

describe("parseUnverifiedRelation (10/23: 核对不过的关系加回到 related)", () => {
  it("认得插件写的「关系：「A」rel「B」」, 普通事实返回 null", async () => {
    const { parseUnverifiedRelation } = await import("./WikiUnverifiedPanel");
    expect(parseUnverifiedRelation("关系：「省工信厅」负责「新兴产业规划」")).toEqual({ rel: "负责", name: "新兴产业规划" });
    expect(parseUnverifiedRelation("关系：「陈耿生」隶属「中电福富（福州）」")).toEqual({ rel: "隶属", name: "中电福富（福州）" });
    expect(parseUnverifiedRelation("2030 年占比 35%")).toBeNull();
  });
});
