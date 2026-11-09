import { describe, expect, it } from "vitest";
import { wikiSourceLabel } from "./wikiLabels";

describe("wikiSourceLabel (9/17 来源粒度)", () => {
  it("journal:日期 → 几月几日日志", () => {
    expect(wikiSourceLabel("journal:2026-07-14")).toBe("7月14日日志");
    expect(wikiSourceLabel('"journal:2026-08-05"')).toBe("8月5日日志");
  });

  it("raw/sources 去时间戳前缀, 标成资料", () => {
    expect(wikiSourceLabel("raw/sources/1783325914-企业资质列表(4)")).toBe("资料「企业资质列表(4)」");
    expect(wikiSourceLabel("wiki/raw/sources/1780620636-周报-陈鸿波-20260529.md")).toBe(
      "资料「周报-陈鸿波-20260529」",
    );
  });

  it("manual 和老的 employee_journal 各有说法", () => {
    expect(wikiSourceLabel("manual")).toBe("手工录入");
    expect(wikiSourceLabel("employee_journal")).toBe("日志（未标日期）");
  });

  it("认不出的原样给回 (只取 basename)", () => {
    expect(wikiSourceLabel("some/other/thing.txt")).toBe("thing.txt");
  });
});

describe("11/02 精确到对话的来源", () => {
  it("session:<id> 显示成哪天几点的对话", () => {
    expect(wikiSourceLabel("session:20261101_091002_aaa111")).toBe("11月1日 09:10 的对话");
    expect(wikiSourceLabel("session:api-abc12345")).toBe("一次对话");
  });
});

describe("11/09 分类", () => {
  const f = (subtype: string | null, title: string, kind = "entity") => ({ subtype, kind, title });

  it("英文类型显示中文; 同一个显示名的合成一组", async () => {
    const { groupByType, ENTITY_TYPE_ORDER } = await import("./wikiLabels");
    const groups = groupByType(
      [f("org", "福富"), f("organization", "中电"), f("strategy", "云改数转"), f("software", "A"), f("软件产品", "B"), f(null, "没类型")],
      ENTITY_TYPE_ORDER,
    );
    expect(groups.map(([label, list]) => [label, list.length])).toEqual([
      ["组织", 2], ["软件产品", 2], ["战略", 1], ["未分类", 1],
    ]);
  });

  it("改分类的候选: 同类里在用的, 按显示名去重取最常用的写法", async () => {
    const { subtypeOptions } = await import("./wikiLabels");
    const opts = subtypeOptions(
      [f("cert", "a"), f("cert", "b"), f("software", "c"), f("软件产品", "d"), f("软件产品", "e"), f("rule", "x", "concept")],
      "entity",
    );
    expect(opts).toEqual([
      { key: "软件产品", label: "软件产品", count: 3 },
      { key: "cert", label: "证书", count: 2 },
    ]);
  });

  it("改 frontmatter 单值字段: 有就换, 没有就加, 没 frontmatter 就补", async () => {
    const { setFrontmatterScalar } = await import("./wikiLabels");
    expect(setFrontmatterScalar("---\ntitle: x\nentity_type: org\n---\n\n正文", "entity_type", "cert"))
      .toBe("---\ntitle: x\nentity_type: cert\n---\n\n正文");
    expect(setFrontmatterScalar("---\ntitle: x\n---\n正文", "entity_type", "cert")).toBe("---\ntitle: x\nentity_type: cert\n---\n正文");
    expect(setFrontmatterScalar("# x\n正文", "entity_type", "cert")).toBe("---\nentity_type: cert\n---\n\n# x\n正文");
  });
});
