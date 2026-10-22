// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { WikiFileInfo } from "../../lib/tauri_wiki";

const files: WikiFileInfo[] = [];
const contents: Record<string, string> = {};
const writes: [string, string][] = [];

const info = (relPath: string, title: string, related: { name: string; rel: string }[]): WikiFileInfo => ({
  rel_path: relPath, kind: "entity", slug: title, title, subtype: null, tags: [], related, sources: [],
  aliases: [], size_bytes: 0, mtime: 0,
});

vi.mock("../../lib/tauri_wiki", () => ({
  wikiReadFile: vi.fn(async (rel: string) => ({
    info: files.find((f) => f.rel_path === rel)!, content: contents[rel], frontmatter: "", body: "正文",
  })),
  wikiUpdateFile: vi.fn(async (rel: string, content: string) => { writes.push([rel, content]); }),
}));
vi.mock("../../lib/wikiRelationSuggest", () => ({
  suggestRelationTypes: vi.fn(async (_entry: WikiFileInfo, _body: string, names: string[]) => ({
    ok: true,
    suggestions: names.map((name) => name === "市场部"
      ? { name, rel: "包含", reason: "公司包含部门" }
      : { name, rel: "关联", reason: "拿不准" }),
  })),
}));
vi.mock("../../store/chat", () => ({ useChatStore: (sel: (s: { model: string }) => unknown) => sel({ model: "m" }) }));
vi.mock("../../store/wiki", () => ({
  useWikiStore: (sel: (s: { files: WikiFileInfo[]; loadFiles: () => Promise<void> }) => unknown) =>
    sel({ files, loadFiles: async () => {} }),
}));

import WikiUntypedBatch from "./WikiUntypedBatch";
import { buildWikiRelationshipTasks } from "./wikiRelationshipTasks";
import { getKeptUntyped } from "../../lib/wikiUntypedKept";

describe("WikiUntypedBatch", () => {
  beforeEach(() => {
    files.length = 0;
    writes.length = 0;
    files.push(
      info("wiki/entities/co.md", "中电福富", [{ name: "市场部", rel: "关联" }, { name: "金智达", rel: "关联" }, { name: "鸿信", rel: "兄弟" }]),
      info("wiki/entities/mkt.md", "市场部", []),
      info("wiki/entities/zd.md", "金智达", []),
      info("wiki/entities/hx.md", "鸿信", []),
    );
    contents["wiki/entities/co.md"] = "---\ntype: entity\ntitle: 中电福富\n---\n\n正文\n";
  });

  it("建议了不写; 采纳只改勾选的那条, 同一条目只写一次; 拿不准的可以一键保持", async () => {
    const task = buildWikiRelationshipTasks(files).find((t) => t.kind === "untyped")!;
    render(<WikiUntypedBatch task={task} />);
    fireEvent.click(screen.getByText("让小鲶全部建议（2 条）"));
    await screen.findByText("公司包含部门");
    expect(writes).toHaveLength(0);

    fireEvent.click(screen.getByText("采纳勾选的 1 条"));
    await waitFor(() => expect(writes).toHaveLength(1));
    const [rel, content] = writes[0];
    expect(rel).toBe("wiki/entities/co.md");
    expect(content).toMatch(/市场部.*包含/);
    expect(content).toMatch(/金智达.*关联/);
    expect(content).toMatch(/鸿信.*兄弟/);

    fireEvent.click(screen.getByText("小鲶说拿不准的 1 条都保持「关联」"));
    expect(getKeptUntyped().has("wiki/entities/co.md::金智达")).toBe(true);
  });
});
