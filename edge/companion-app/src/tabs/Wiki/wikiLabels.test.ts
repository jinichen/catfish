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
