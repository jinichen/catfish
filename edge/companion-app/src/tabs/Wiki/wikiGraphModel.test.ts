import { describe, expect, it } from "vitest";
import type { WikiFileInfo } from "../../lib/tauri";
import {
  buildWikiGraphModel,
  buildWikiGraphOverview,
  collectEgoPaths,
  limitWikiGraphPaths,
} from "./wikiGraphModel";

function wiki(
  relPath: string,
  title: string,
  related: WikiFileInfo["related"] = [],
): WikiFileInfo {
  return {
    rel_path: relPath,
    kind: "entity",
    slug: title,
    title,
    subtype: null,
    tags: [],
    related,
    sources: [],
    aliases: [],
    size_bytes: 1,
    mtime: 0,
  };
}

describe("wikiGraphModel", () => {
  it("limits a high-degree anchor while keeping the anchor and direct relations", () => {
    const files = [
      wiki("company", "公司", [
        { name: "流程", rel: "执行" },
        { name: "制度", rel: "遵循" },
      ]),
      wiki("process", "流程"),
      wiki("policy", "制度"),
      wiki("person-a", "人员甲", [{ name: "公司", rel: "任职于" }]),
      wiki("person-b", "人员乙", [{ name: "公司", rel: "任职于" }]),
    ];
    const model = buildWikiGraphModel(files);
    const ego = collectEgoPaths(model, "company");
    const visible = limitWikiGraphPaths(model, ego, "company", 2);

    expect(ego.size).toBe(5);
    expect(visible.size).toBe(3);
    expect(visible.has("company")).toBe(true);
    expect(visible.has("process")).toBe(true);
    expect(visible.has("policy")).toBe(true);
  });

  it("builds readable outgoing and incoming overview rows from visible nodes", () => {
    const files = [
      wiki("company", "公司", [{ name: "流程", rel: "执行" }]),
      wiki("process", "流程"),
      wiki("person", "人员", [{ name: "公司", rel: "任职于" }]),
    ];
    const model = buildWikiGraphModel(files);
    const visible = collectEgoPaths(model, "company");
    const overview = buildWikiGraphOverview(model, "company", visible);

    expect(overview).toEqual([
      { targetPath: "process", title: "流程", relation: "执行", direction: "outgoing" },
      { targetPath: "person", title: "人员", relation: "任职于", direction: "incoming" },
    ]);
  });
});
