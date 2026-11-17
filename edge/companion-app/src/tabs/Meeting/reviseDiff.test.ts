import { describe, expect, it } from "vitest";

import { diffItems, diffList, itemKey, markdownHunks, reviseSuggestions } from "./reviseDiff";

describe("diffList", () => {
  it("保留顺序: 删掉的在原位置, 新加的在新位置", () => {
    expect(diffList(["a", "b", "c"], ["a", "x", "c", "d"]).map((r) => `${r.kind[0]}${r.value}`))
      .toEqual(["sa", "rb", "ax", "sc", "ad"]);
  });
  it("空 / 全换", () => {
    expect(diffList([], [])).toEqual([]);
    expect(diffList(["a"], ["b"]).map((r) => r.kind)).toEqual(["removed", "added"]);
  });
  it("待办按事项 + 负责人 + 截止比", () => {
    const a = [{ task: "交材料", owner: "说话人2", due: "" }];
    const b = [{ task: "交材料", owner: "王总", due: "" }];
    expect(diffList(a, b, itemKey).map((r) => r.kind)).toEqual(["removed", "added"]);
    expect(diffList(a, a, itemKey).map((r) => r.kind)).toEqual(["same"]);
  });
});

describe("diffItems", () => {
  it("事项一样只改负责人 / 截止 → 一行 changed, 带改前的", () => {
    const a = [{ task: "交材料", owner: "说话人2", due: "" }, { task: "约客户", owner: "", due: "" }];
    const b = [{ task: "交材料", owner: "王总", due: "2026-11-30" }, { task: "补证据", owner: "", due: "" }];
    const rows = diffItems(a, b);
    expect(rows.map((r) => r.kind)).toEqual(["changed", "removed", "added"]);
    expect(rows[0].old?.owner).toBe("说话人2");
    expect(rows[0].value.owner).toBe("王总");
  });
});

describe("markdownHunks", () => {
  it("只留改动和前后一行, 中间折成 null", () => {
    const before = ["# 标题", "1", "2", "3", "4", "5", "旧", "6"].join("\n");
    const after = ["# 新标题", "1", "2", "3", "4", "5", "新", "6"].join("\n");
    const h = markdownHunks(before, after);
    expect(h.map((r) => (r === null ? "…" : `${r.kind[0]}${r.value}`)))
      .toEqual(["r# 标题", "a# 新标题", "s1", "…", "s5", "r旧", "a新", "s6"]);
  });
  it("没改: 空", () => {
    expect(markdownHunks("a\nb\n", "a\nb")).toEqual([]);
  });
});

describe("reviseSuggestions", () => {
  const m = { meeting_id: "m", summary: "", decisions: [], open_questions: [],
              action_items: [{ task: "交材料", owner: "说话人2", due: "" }] };
  it("起过名字、纪要里还是说话人编号 → 第一条就是换成真名, 带上对照", () => {
    expect(reviseSuggestions(m, "", { "1": "王总", "0": "" })[0]).toBe("把纪要里的说话人编号换成真名 (说话人2=王总)");
  });
  it("没起名字 → 教怎么说", () => {
    expect(reviseSuggestions(m, "", {})[0]).toMatch(/^说话人3 是 ×××/);
  });
  it("纪要里没有说话人编号 → 不出这条", () => {
    const s = reviseSuggestions({ ...m, action_items: [] }, "# 周会", { "0": "王总" });
    expect(s.some((x) => x.includes("说话人"))).toBe(false);
  });
});
