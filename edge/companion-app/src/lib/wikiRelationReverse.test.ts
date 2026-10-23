import { beforeEach, describe, expect, it, vi } from "vitest";
import type { WikiFileInfo } from "./tauri_wiki";

const files: WikiFileInfo[] = [];
const contents: Record<string, string> = {};
const writes: [string, string][] = [];
let failOn: string | null = null;

const info = (relPath: string, title: string, related: { name: string; rel: string }[]): WikiFileInfo => ({
  rel_path: relPath, kind: "entity", slug: title, title, subtype: null, tags: [], related, sources: [],
  aliases: [], size_bytes: 0, mtime: 0,
});

vi.mock("./tauri_wiki", () => ({
  wikiReadFile: vi.fn(async (rel: string) => ({
    info: files.find((f) => f.rel_path === rel)!, content: contents[rel], frontmatter: "", body: "",
  })),
  wikiUpdateFile: vi.fn(async (rel: string, content: string) => {
    if (rel === failOn) throw new Error("写不进去");
    writes.push([rel, content]);
  }),
}));

import { applyReverseMoves } from "./wikiRelationReverse";

describe("applyReverseMoves", () => {
  beforeEach(() => {
    files.length = 0;
    writes.length = 0;
    failOn = null;
    files.push(
      info("wiki/entities/cert.md", "CMMI-5", [{ name: "中电福富", rel: "关联" }, { name: "SPCA", rel: "同类" }]),
      info("wiki/entities/co.md", "中电福富", [{ name: "ISO 9001", rel: "持有" }]),
    );
    for (const f of files) contents[f.rel_path] = `---\ntype: entity\ntitle: ${f.title}\n---\n\n正文\n`;
  });

  it("对方记上「持有 本条目」, 本条目删掉这条, 别的关系不动", async () => {
    const { moved, failed } = await applyReverseMoves([{ source: files[0], name: "中电福富", rel: "持有" }], files);
    expect(failed).toEqual([]);
    expect(moved).toHaveLength(1);
    const co = writes.find(([rel]) => rel === "wiki/entities/co.md")![1];
    expect(co).toMatch(/ISO 9001.*持有/);
    expect(co).toMatch(/CMMI-5.*持有/);
    const cert = writes.find(([rel]) => rel === "wiki/entities/cert.md")![1];
    expect(cert).not.toContain("中电福富");
    expect(cert).toMatch(/SPCA.*同类/);
  });

  it("对方写失败就不从本条目删", async () => {
    failOn = "wiki/entities/co.md";
    const { moved, failed } = await applyReverseMoves([{ source: files[0], name: "中电福富", rel: "持有" }], files);
    expect(moved).toEqual([]);
    expect(failed).toHaveLength(1);
    expect(writes).toEqual([]);
  });

  it("对方条目不存在就报出来, 什么都不写", async () => {
    const { failed } = await applyReverseMoves([{ source: files[0], name: "不存在", rel: "持有" }], files);
    expect(failed[0]).toContain("找不到");
    expect(writes).toEqual([]);
  });
});
