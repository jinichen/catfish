import { describe, expect, it } from "vitest";
import { parseWikiActionRefs, writeWikiActionRefs } from "./wikiActions";

describe("Wiki action refs", () => {
  it("parses quoted inline refs and removes duplicates", () => {
    expect(parseWikiActionRefs('title: x\naction_refs: ["skill.a", "skill.a", skill.b]')).toEqual([
      "skill.a",
      "skill.b",
    ]);
  });

  it("writes only action_refs and preserves the body", () => {
    const source = "---\ntitle: x\ntags: [a]\n---\n\n正文\n";
    const output = writeWikiActionRefs(source, ["skill.a", "skill.a"]);
    expect(output).toContain('action_refs: ["skill.a"]');
    expect(output).toContain("tags: [a]");
    expect(output).toContain("正文");
  });

  it("rejects unsafe ids", () => {
    expect(() => writeWikiActionRefs("---\ntitle: x\n---\n", ["../shell"])).toThrow(
      "action_id",
    );
  });
});
