import { describe, expect, it } from "vitest";

import type { WikiFileInfo } from "../../lib/tauri_wiki";
import { actionItemToTask, estimatePercent, mergeHotwords, wikiHotwords } from "./meetingHelpers";

function entry(p: Partial<WikiFileInfo>): WikiFileInfo {
  return {
    rel_path: "wiki/entities/x.md", kind: "entity", slug: "x", title: "x", subtype: null, tags: [],
    related: [], sources: [], aliases: [], size_bytes: 0, mtime: 0, ...p,
  } as WikiFileInfo;
}

describe("wikiHotwords", () => {
  it("取人名 / 项目 / 单位的标题和别名, 去重", () => {
    const files = [
      entry({ title: "中电福富", subtype: "org", aliases: ["中电福富信息科技有限公司", "福富"] }),
      entry({ title: "陈秀平", subtype: "person" }),
      entry({ title: "资质集采二期", subtype: "project", aliases: ["福富"] }),
      entry({ title: "CSMM-4 评估方法", subtype: "method" }), // 不是人/项目/单位
      entry({ title: "某概念", kind: "concept", subtype: "person" }),
      entry({ title: "待确认的人", subtype: "person", ontology_status: "pending" }),
    ];
    expect(wikiHotwords(files)).toEqual(["中电福富", "中电福富信息科技有限公司", "福富", "陈秀平", "资质集采二期"]);
  });

  it("太长的不要, 最多 100 个", () => {
    const long = entry({ title: "这是一个特别特别特别特别特别特别长的标题不像热词", subtype: "project" });
    expect(wikiHotwords([long])).toEqual([]);
    const many = Array.from({ length: 150 }, (_, i) => entry({ title: `人${i}`, subtype: "person" }));
    expect(wikiHotwords(many)).toHaveLength(100);
  });
});

describe("mergeHotwords", () => {
  it("保留已填的, 追加没有的", () => {
    expect(mergeHotwords("达华，福富", ["福富", "高新"])).toBe("达华 福富 高新");
    expect(mergeHotwords("", ["a"])).toBe("a");
  });
});

describe("actionItemToTask", () => {
  it("带来源和序号, 空截止日期不传", () => {
    expect(actionItemToTask("mtg_x", "周会", 2, { owner: "张三", task: "交材料", due: "2026-10-03" })).toEqual({
      title: "交材料", due_date_iso: "2026-10-03", body: "负责人: 张三\n来自会议「周会」的纪要",
      source: "meeting", source_id: "mtg_x#2",
    });
    expect(actionItemToTask("mtg_x", "周会", 0, { owner: "", task: "t", due: "" }).due_date_iso).toBeUndefined();
  });
});

describe("estimatePercent", () => {
  it("按 8 倍实时估, 封顶 95", () => {
    expect(estimatePercent(15, 240)).toBe(50);
    expect(estimatePercent(999, 240)).toBe(95);
    expect(estimatePercent(10, null)).toBeNull();
  });
});
