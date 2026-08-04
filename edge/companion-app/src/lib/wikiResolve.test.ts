/** resolveWikiRef 的契约测试 —— 用例来自 edge/contracts/wiki_resolve_cases.json,
 *  Python 侧 test_wiki_resolve.py 读的是同一个文件。
 *
 *  两侧对拍的先例见 wiki_visibility_cases.json: 之前 Rust 和 Python 对
 *  "哪些条目该显示" 的判断悄悄分叉过, 靠共享 JSON 才钉住。
 */

import { describe, expect, it } from "vitest";
import cases from "../../../contracts/wiki_resolve_cases.json";
import { resolveWikiRef, auditWikiRefs } from "./wikiResolve";
import type { WikiFileInfo } from "./tauri";

const files: WikiFileInfo[] = (cases.files as Array<Record<string, unknown>>).map(
  (f) =>
    ({
      rel_path: f.rel_path,
      kind: "entity",
      slug: f.slug,
      title: f.title,
      subtype: null,
      tags: [],
      related: [],
      sources: [],
      aliases: f.aliases,
      size_bytes: 100,
      mtime: 0,
    }) as WikiFileInfo,
);

describe("resolveWikiRef · 共享契约", () => {
  for (const c of cases.cases as Array<Record<string, never>>) {
    const q = c.query as unknown as string;
    const want = c.expect as unknown as Record<string, unknown>;
    it(c.name as unknown as string, () => {
      const got = resolveWikiRef(q, files);
      expect(got.kind).toBe(want.kind);
      if (want.kind === "hit" && got.kind === "hit") {
        expect(got.file.rel_path).toBe(want.rel_path);
        expect(got.how).toBe(want.how);
      }
      if (want.kind === "ambiguous" && got.kind === "ambiguous") {
        expect(got.candidates.map((x) => x.title).sort()).toEqual(
          (want.candidates as string[]).slice().sort(),
        );
      }
    });
  }
});

describe("结果不依赖 files 的顺序", () => {
  it("倒序传入结果完全一致 —— 老实现在这里会变", () => {
    // 老 findTarget 用 files.find(...includes...), 而 wiki_list_files 按 mtime
    // 倒序排 —— 数组一换顺序, 同一个名字就指向另一个条目。这条是那个 bug 的钉子。
    for (const c of cases.cases as Array<Record<string, never>>) {
      const q = c.query as unknown as string;
      const a = resolveWikiRef(q, files);
      const b = resolveWikiRef(q, [...files].reverse());
      expect(b.kind).toBe(a.kind);
      if (a.kind === "hit" && b.kind === "hit") {
        expect(b.file.rel_path).toBe(a.file.rel_path);
      }
    }
  });
});

describe("auditWikiRefs", () => {
  it("断链和歧义分开报 —— 两种病不一样", () => {
    const withEdges: WikiFileInfo[] = [
      ...files,
      {
        ...files[0],
        rel_path: "wiki/entities/z.md",
        slug: "z",
        title: "引用方",
        aliases: [],
        related: [
          { name: "中电福富" },        // 别名命中
          { name: "根本没有这个" },     // 断链
          { name: "资质" },            // 歧义
        ],
      } as WikiFileInfo,
    ];
    const r = auditWikiRefs(withEdges);
    expect(r.total).toBe(3);
    expect(r.hits).toBe(1);
    expect(r.misses.map((m) => m.name)).toEqual(["根本没有这个"]);
    expect(r.ambiguous).toHaveLength(1);
    expect(r.ambiguous[0].candidates.sort()).toEqual(
      ["ITSS 运维资质", "高新资质申报"].sort(),
    );
  });
});
